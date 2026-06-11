"""
TokenBlacklist — progressive drift-based token suppression.

Escalation levels per token:
  Level 1: 30 minutes  (1st offence)
  Level 2: 2 hours     (2nd offence)
  Level 3: 12 hours    (3rd offence)
  Level 4: permanent   (4th+ offence — session-scoped, max 100 tokens)

A token triggers escalation when it accumulates BLACKLIST_DRIFT_THRESHOLD
PRE_TRADE_CANCELLED events within BLACKLIST_DRIFT_WINDOW seconds.

Thread-safety: single-threaded asyncio loop — no locks needed.
"""

import time
from collections import defaultdict
from .config import (
    BLACKLIST_DRIFT_WINDOW,
    BLACKLIST_DRIFT_THRESHOLD,
)
from .logger import logger

# Progressive durations (seconds)
_LEVELS = {
    1: 30 * 60,          # 30 minutes
    2: 2 * 60 * 60,      # 2 hours
    3: 12 * 60 * 60,     # 12 hours
    4: float("inf"),     # permanent (session)
}
_LEVEL_LABELS = {
    1: "30min",
    2: "2h",
    3: "12h",
    4: "PERMANENT",
}
_MAX_PERMANENT = 100     # cap on permanently blacklisted tokens


def _duration_label(seconds: float) -> str:
    if seconds == float("inf"):
        return "PERMANENT"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}m" if h else f"{m}min"


class TokenBlacklist:
    def __init__(self):
        # {token_id: [timestamp, ...]}  — rolling window of drift events per token
        self._drift_events: dict[str, list[float]] = defaultdict(list)

        # {token_id: {'until': float, 'level': int, 'last_drift_pct': float,
        #             'trigger_count': int, 'cancel_count': int}}
        self._blacklist: dict[str, dict] = {}

        # {token_id: int}  — lifetime escalation level per token (persists across re-admissions)
        self._escalation_level: dict[str, int] = {}

        # Set of tokens on permanent blacklist (level 4)
        self._permanent: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_blacklisted(self, token_id: str) -> bool:
        """
        Return True if the token is currently blacklisted.
        Permanent (level 4) tokens never expire.
        Timed tokens auto-expire and log re-admission.
        """
        entry = self._blacklist.get(token_id)
        if entry is None:
            return False

        if entry["until"] == float("inf"):
            return True  # permanent — never expires

        if time.time() >= entry["until"]:
            level = entry["level"]
            drift = entry["last_drift_pct"]
            del self._blacklist[token_id]
            logger.info(
                f"TOKEN_BLACKLIST_EXPIRED: {token_id[:20]}... | "
                f"level={level} ({_LEVEL_LABELS[level]}) | "
                f"last_drift={drift:.2f}% | "
                f"Token re-admitted — escalation persists (next={level+1})"
            )
            return False
        return True

    def record_drift_cancel(self, token_id: str, drift_pct: float) -> bool:
        """
        Record a PRE_TRADE_CANCELLED drift event for this token.
        Escalates blacklist level if threshold is reached within the window.

        Returns True if the token was just blacklisted by this call.
        """
        now = time.time()

        # Update cancel count on existing entry (even if already blacklisted)
        if token_id in self._blacklist:
            self._blacklist[token_id]["cancel_count"] = \
                self._blacklist[token_id].get("cancel_count", 0) + 1
            return False  # already blacklisted — don't re-trigger

        # Purge events outside rolling window
        window_start = now - BLACKLIST_DRIFT_WINDOW
        self._drift_events[token_id] = [
            t for t in self._drift_events[token_id] if t >= window_start
        ]
        self._drift_events[token_id].append(now)

        count = len(self._drift_events[token_id])

        if count < BLACKLIST_DRIFT_THRESHOLD:
            return False  # not yet at threshold

        # --- Threshold reached: escalate ---
        current_level = self._escalation_level.get(token_id, 0)
        next_level    = min(current_level + 1, 4)
        duration      = _LEVELS[next_level]

        # Cap permanent blacklist
        if duration == float("inf") and len(self._permanent) >= _MAX_PERMANENT:
            logger.warning(
                f"TOKEN_BLACKLIST_CAP_REACHED: {token_id[:20]}... | "
                f"Permanent blacklist full ({_MAX_PERMANENT} tokens). "
                f"Falling back to level-3 (12h)."
            )
            next_level = 3
            duration   = _LEVELS[3]

        self._escalation_level[token_id] = next_level
        until = now + duration

        self._blacklist[token_id] = {
            "until":          until,
            "level":          next_level,
            "last_drift_pct": drift_pct,
            "trigger_count":  count,
            "cancel_count":   0,
        }

        if duration == float("inf"):
            self._permanent.add(token_id)

        label = _LEVEL_LABELS[next_level]
        dur_str = _duration_label(duration)

        if next_level == 1:
            log_key = "TOKEN_TEMP_BLACKLISTED"
        else:
            log_key = "TOKEN_BLACKLIST_ESCALATED"

        logger.warning(
            f"{log_key}: {token_id} | "
            f"level={next_level} ({label}) | "
            f"duration={dur_str} | "
            f"reason=drift | "
            f"last_drift={drift_pct:.2f}% | "
            f"cancel_count={count}/{BLACKLIST_DRIFT_THRESHOLD} in {BLACKLIST_DRIFT_WINDOW}s"
        )

        # Reset rolling window so re-admission starts fresh
        self._drift_events[token_id] = []
        return True

    def stats(self) -> dict:
        """Return a snapshot of current blacklist state."""
        now = time.time()
        active = {}
        for tid, entry in self._blacklist.items():
            remaining = entry["until"] - now
            active[tid[:20]] = {
                "level":          entry["level"],
                "label":          _LEVEL_LABELS[entry["level"]],
                "remaining_s":    remaining if remaining != float("inf") else "∞",
                "last_drift_pct": entry["last_drift_pct"],
            }
        return {
            "active_count":    len(active),
            "permanent_count": len(self._permanent),
            "tokens":          active,
        }

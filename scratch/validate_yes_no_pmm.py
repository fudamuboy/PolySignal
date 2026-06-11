import asyncio
import random
import sqlite3
import json
import copy
import sys
import os

# Deterministic seed for reproducible testing
random.seed(123)

class YES_NO_Simulator:
    def __init__(self, capital=500.0, order_size=15.0, fee_bps=20.0):
        self.capital = capital
        self.cash = capital
        self.order_size = order_size
        self.fee = fee_bps / 10000.0
        
        # Position tracking: {pair_id: {'YES': size, 'YES_price': float, 'NO': size, 'NO_price': float}}
        self.positions = {} 
        
        # Metrics
        self.completed_cycles = 0
        self.realized_pnl = 0.0
        self.peak_equity = capital
        self.max_drawdown = 0.0
        self.leg_risk_count = 0
        self.hedge_count = 0
        self.failed_hedge_count = 0
        self.spreads_captured = []
        self.round_trip_pnls = []
        self.capital_recycled_volume = 0.0
        
        # Active orders state: {pair_id: {'YES': order_dict, 'NO': order_dict}}
        self.resting_bids = {}
        self.resting_asks = {}

    def get_equity(self, market_prices):
        """Calculate total account equity = cash + market value of positions."""
        pos_value = 0.0
        for pair_id, pos in self.positions.items():
            yes_price = market_prices[pair_id]["YES"]["mid"]
            no_price = market_prices[pair_id]["NO"]["mid"]
            pos_value += pos.get("YES", 0.0) * yes_price + pos.get("NO", 0.0) * no_price
        return self.cash + pos_value

    def record_drawdown(self, equity):
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = self.peak_equity - equity
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def run_tick(self, market_data, tick_index):
        """
        Stateful simulation step.
        market_data: {pair_id: {'YES': {'bid': f, 'ask': f, 'mid': f, 'spread': f}, 'NO': ...}}
        """
        # Create a local copy to manipulate pricing or skews
        quoted_market = copy.deepcopy(market_data)
        
        # 1. Update existing resting SELL asks (if we hold both sides, we place passive asks to exit)
        for pair_id in list(self.resting_asks.keys()):
            pos = self.positions.get(pair_id)
            if not pos or pos.get("YES", 0.0) <= 0.0 or pos.get("NO", 0.0) <= 0.0:
                # Mismatch occurred or position was already closed, clean up asks
                if pair_id in self.resting_asks:
                    del self.resting_asks[pair_id]
                continue
                
            mdata = market_data[pair_id]
            yes_ask_price = mdata["YES"]["ask"]
            no_ask_price = mdata["NO"]["ask"]
            
            # Simple passive fill heuristic: 40% chance of getting filled at the ask per loop
            # simulating queue completion.
            if random.random() < 0.40:
                # Fills! Exit both passive asks
                yes_size = pos["YES"]
                no_size = pos["NO"]
                
                # Revenue minus exit maker fees
                yes_revenue = yes_size * yes_ask_price * (1.0 - self.fee)
                no_revenue = no_size * no_ask_price * (1.0 - self.fee)
                
                total_revenue = yes_revenue + no_revenue
                cost = (yes_size * pos["YES_price"]) + (no_size * pos["NO_price"])
                pnl = total_revenue - cost
                
                self.cash += total_revenue
                self.realized_pnl += pnl
                self.round_trip_pnls.append(pnl)
                self.completed_cycles += 1
                self.capital_recycled_volume += cost
                
                # Record captured spread
                spread = mdata["YES"]["spread"] + mdata["NO"]["spread"]
                self.spreads_captured.append(spread)
                
                # Clear position and resting orders
                del self.positions[pair_id]
                del self.resting_asks[pair_id]
                continue

        # 2. Check resting BUY limit orders
        for pair_id in list(self.resting_bids.keys()):
            pos = self.positions.get(pair_id, {"YES": 0.0, "YES_price": 0.0, "NO": 0.0, "NO_price": 0.0})
            mdata = market_data[pair_id]
            
            resting = self.resting_bids[pair_id]
            
            # Case A: We hold neither leg, and are passively waiting for first-leg fill
            if pos["YES"] == 0.0 and pos["NO"] == 0.0:
                # 35% chance to fill YES passively first, 35% to fill NO first
                r = random.random()
                if r < 0.35:
                    # YES fills passively at bid price
                    bid_price = mdata["YES"]["bid"]
                    size = self.order_size / bid_price
                    cost = size * bid_price * (1.0 + self.fee)
                    
                    if cost <= self.cash:
                        self.cash -= cost
                        pos["YES"] = size
                        pos["YES_price"] = bid_price
                        self.positions[pair_id] = pos
                        
                        # Leg Risk Triggered!
                        self.leg_risk_count += 1
                        resting["YES_filled"] = True
                        resting["fill_tick"] = tick_index
                        # Skew NO bid UP by 50% of the spread to sitting at the inside bid to force fill
                        resting["NO_skew"] = mdata["NO"]["spread"] * 0.50
                elif r < 0.70:
                    # NO fills passively at bid price
                    bid_price = mdata["NO"]["bid"]
                    size = self.order_size / bid_price
                    cost = size * bid_price * (1.0 + self.fee)
                    
                    if cost <= self.cash:
                        self.cash -= cost
                        pos["NO"] = size
                        pos["NO_price"] = bid_price
                        self.positions[pair_id] = pos
                        
                        # Leg Risk Triggered!
                        self.leg_risk_count += 1
                        resting["NO_filled"] = True
                        resting["fill_tick"] = tick_index
                        # Skew YES bid UP by 50% of the spread
                        resting["YES_skew"] = mdata["YES"]["spread"] * 0.50
            
            # Case B: We hold YES passively, waiting for NO leg to fill
            elif pos["YES"] > 0.0 and pos["NO"] == 0.0:
                ticks_since_fill = tick_index - resting.get("fill_tick", tick_index)
                
                # Check for active hedge trigger (stale for 3 ticks or price drift)
                if ticks_since_fill >= 3 or random.random() < 0.15:
                    # Trigger Active Taker Hedge! Buy NO at the ask price immediately
                    ask_price = mdata["NO"]["ask"]
                    size = pos["YES"] # hedge size matched exactly to YES size
                    
                    # Add mismatch / failed hedge risk: 3% probability that the hedge order gets slipped
                    # or liquidity crashes, resulting in a toxic partial execution or large slippage cost.
                    hedge_slippage = 0.0
                    if random.random() < 0.03:
                        self.failed_hedge_count += 1
                        hedge_slippage = random.uniform(0.02, 0.06) # 2% to 6% slippage loss
                        
                    hedge_price = ask_price * (1.0 + hedge_slippage)
                    cost = size * hedge_price * (1.0 + self.fee)  # taker order pays fee
                    
                    if cost <= self.cash:
                        self.cash -= cost
                        pos["NO"] = size
                        pos["NO_price"] = hedge_price
                        self.positions[pair_id] = pos
                        self.hedge_count += 1
                        
                        # Clear bids, transition to active asks to recycle capital
                        del self.resting_bids[pair_id]
                        self.resting_asks[pair_id] = True
                    else:
                        # Insufficient cash for taker hedge - force emergency liquidation of YES at a loss
                        bid_price = mdata["YES"]["bid"]
                        revenue = pos["YES"] * bid_price * (1.0 - self.fee)
                        pnl = revenue - (pos["YES"] * pos["YES_price"])
                        self.cash += revenue
                        self.realized_pnl += pnl
                        self.round_trip_pnls.append(pnl)
                        self.completed_cycles += 1
                        del self.positions[pair_id]
                        del self.resting_bids[pair_id]
                        
                else:
                    # 50% chance of passive fill due to our skewed bid price
                    skewed_bid = mdata["NO"]["bid"] + resting.get("NO_skew", 0.0)
                    if random.random() < 0.50:
                        size = pos["YES"]
                        cost = size * skewed_bid * (1.0 + self.fee)
                        if cost <= self.cash:
                            self.cash -= cost
                            pos["NO"] = size
                            pos["NO_price"] = skewed_bid
                            self.positions[pair_id] = pos
                            
                            # Both filled, clear bids and place asks
                            del self.resting_bids[pair_id]
                            self.resting_asks[pair_id] = True

            # Case C: We hold NO passively, waiting for YES leg to fill
            elif pos["NO"] > 0.0 and pos["YES"] == 0.0:
                ticks_since_fill = tick_index - resting.get("fill_tick", tick_index)
                
                # Active taker hedge trigger
                if ticks_since_fill >= 3 or random.random() < 0.15:
                    ask_price = mdata["YES"]["ask"]
                    size = pos["NO"]
                    
                    hedge_slippage = 0.0
                    if random.random() < 0.03:
                        self.failed_hedge_count += 1
                        hedge_slippage = random.uniform(0.02, 0.06)
                        
                    hedge_price = ask_price * (1.0 + hedge_slippage)
                    cost = size * hedge_price * (1.0 + self.fee)
                    
                    if cost <= self.cash:
                        self.cash -= cost
                        pos["YES"] = size
                        pos["YES_price"] = hedge_price
                        self.positions[pair_id] = pos
                        self.hedge_count += 1
                        
                        del self.resting_bids[pair_id]
                        self.resting_asks[pair_id] = True
                    else:
                        # Insufficient cash for taker hedge - liquidate NO
                        bid_price = mdata["NO"]["bid"]
                        revenue = pos["NO"] * bid_price * (1.0 - self.fee)
                        pnl = revenue - (pos["NO"] * pos["NO_price"])
                        self.cash += revenue
                        self.realized_pnl += pnl
                        self.round_trip_pnls.append(pnl)
                        self.completed_cycles += 1
                        del self.positions[pair_id]
                        del self.resting_bids[pair_id]
                else:
                    # 50% chance of passive fill on skewed bid
                    skewed_bid = mdata["YES"]["bid"] + resting.get("YES_skew", 0.0)
                    if random.random() < 0.50:
                        size = pos["NO"]
                        cost = size * skewed_bid * (1.0 + self.fee)
                        if cost <= self.cash:
                            self.cash -= cost
                            pos["YES"] = size
                            pos["YES_price"] = skewed_bid
                            self.positions[pair_id] = pos
                            
                            del self.resting_bids[pair_id]
                            self.resting_asks[pair_id] = True

        # 3. Place new passive bids on empty markets if we have cash
        for pair_id in market_data.keys():
            if pair_id not in self.positions and pair_id not in self.resting_bids:
                # We need cash to back BOTH bids passively
                # Bids are roughly mid-price, so we need order_size * 2 cash buffer
                cash_required = self.order_size * 2
                if self.cash >= cash_required:
                    self.resting_bids[pair_id] = {
                        "YES_filled": False,
                        "NO_filled": False,
                        "YES_skew": 0.0,
                        "NO_skew": 0.0,
                        "placed_tick": tick_index
                    }
                    
        # Update drawdowns
        self.record_drawdown(self.get_equity(market_data))


class DirectionalSimulator:
    """Current PMM Simulator (sniping directional momentum/reversion)."""
    def __init__(self, capital=500.0, order_size=10.0, fee_bps=20.0):
        self.capital = capital
        self.cash = capital
        self.order_size = order_size
        self.fee = fee_bps / 10000.0
        
        self.positions = {} # {token_id: {'size': float, 'avg_price': float, 'strategy': str}}
        self.completed_cycles = 0
        self.realized_pnl = 0.0
        self.peak_equity = capital
        self.max_drawdown = 0.0
        self.round_trip_pnls = []
        self.capital_recycled_volume = 0.0
        
        self.cooldowns = {} # {token_id: tick_index}

    def get_equity(self, market_data):
        pos_value = 0.0
        for tok_id, pos in self.positions.items():
            price = market_data[tok_id]["mid"]
            pos_value += pos["size"] * price
        return self.cash + pos_value

    def record_drawdown(self, equity):
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = self.peak_equity - equity
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def run_tick(self, market_data, tick_index, pre_generated_signals):
        """
        Current PMM tick logic.
        market_data: {token_id: {'bid': f, 'ask': f, 'mid': f, 'spread': f}}
        """
        # 1. Update open positions exits (TP/SL)
        for tok_id, pos in list(self.positions.items()):
            mdata = market_data.get(tok_id)
            if not mdata:
                continue
            
            entry_price = pos["avg_price"]
            curr_mid = mdata["mid"]
            curr_bid = mdata["bid"]
            
            change = (curr_mid - entry_price) / entry_price
            
            # Stop Loss (5%) or Take Profit (7%)
            is_sl = change <= -0.05
            is_tp = change >= 0.07
            
            if is_sl or is_tp:
                # Sell passively/aggressively at the bid
                sell_price = curr_bid
                revenue = pos["size"] * sell_price * (1.0 - self.fee)
                cost = pos["size"] * entry_price
                pnl = revenue - cost
                
                self.cash += revenue
                self.realized_pnl += pnl
                self.round_trip_pnls.append(pnl)
                self.completed_cycles += 1
                self.capital_recycled_volume += cost
                
                del self.positions[tok_id]
                self.cooldowns[tok_id] = tick_index + 60  # 60-tick cooldown (approx 10 min)
                continue

        # 2. Check for new BUY signals
        for sig in pre_generated_signals:
            tok_id = sig["token_id"]
            if tok_id in self.positions:
                continue
            if tick_index < self.cooldowns.get(tok_id, 0):
                continue
                
            mdata = market_data.get(tok_id)
            if not mdata:
                continue
                
            # Passive maker BUY EV check (Upgrade 1 Active)
            # Maker required edge = fees * 2 + safety margin = 0.4% + 0.1% = 0.5%
            delta_pct = sig["delta"] / mdata["mid"]
            required_edge = (self.fee * 2) + 0.001
            
            # EV passes!
            if delta_pct >= required_edge:
                buy_price = mdata["mid"] # buy at bid/mid passively
                size = self.order_size / buy_price
                cost = size * buy_price * (1.0 + self.fee)
                
                if cost <= self.cash:
                    self.cash -= cost
                    self.positions[tok_id] = {
                        "size": size,
                        "avg_price": buy_price
                    }
                    
        # Update drawdowns
        self.record_drawdown(self.get_equity(market_data))


def pre_generate_simulation_data(seeds=[123], steps=20000):
    """
    Generate deterministically aligned prices, spreads, and signals for both simulators.
    """
    random.seed(seeds[0])
    
    # 5 outcome pairs = 10 tokens
    pairs = [f"pair_{i}" for i in range(5)]
    tokens = []
    for p in pairs:
        tokens.extend([f"{p}_YES", f"{p}_NO"])
        
    prices = {t: 0.50 for t in tokens}
    
    ticks_data = []
    signals_data = []
    
    for tick in range(steps):
        # 1. Price walk step
        for p in pairs:
            # Symmetrical price walk for YES/NO pairs to keep P(YES) + P(NO) = 1.00
            move = random.normalvariate(0, 0.015)
            prices[f"{p}_YES"] += move
            prices[f"{p}_YES"] = max(0.12, min(0.88, prices[f"{p}_YES"]))
            prices[f"{p}_NO"] = 1.0 - prices[f"{p}_YES"]
            
        # 2. Market spreads & depth
        market_tick = {}
        for t in tokens:
            spread_abs = random.uniform(0.01, 0.06)  # 1% to 6% spread
            mid = prices[t]
            bid = round(mid - spread_abs/2, 4)
            ask = round(mid + spread_abs/2, 4)
            spread = spread_abs / mid
            
            market_tick[t] = {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread
            }
            
        # Group tokens into pair structure for the YES/NO PMM
        pair_tick = {}
        for p in pairs:
            pair_tick[p] = {
                "YES": market_tick[f"{p}_YES"],
                "NO": market_tick[f"{p}_NO"]
            }
            
        ticks_data.append((market_tick, pair_tick))
        
        # 3. Generate trade signals for the Directional PMM
        tick_signals = []
        for t in tokens:
            if random.random() < 0.05:  # 5% chance per tick to generate signal
                delta_pct = random.uniform(0.015, 0.065)
                tick_signals.append({
                    "token_id": t,
                    "delta": delta_pct * prices[t],
                    "score": random.uniform(70, 95)
                })
        signals_data.append(tick_signals)
        
    return ticks_data, signals_data


async def main():
    print("Pre-generating identical price walks and signals (20,000 steps)...")
    ticks_data, signals_data = pre_generate_simulation_data(seeds=[123], steps=20000)
    
    print("\n[1/2] Simulating CURRENT PMM (Directional Sniping + EV Maker)...")
    dir_sim = DirectionalSimulator(capital=500.0, order_size=10.0, fee_bps=20.0)
    for tick in range(len(ticks_data)):
        dir_sim.run_tick(ticks_data[tick][0], tick, signals_data[tick])
        # Stop at 1000 completed round trips
        if dir_sim.completed_cycles >= 1000:
            break
            
    print("\n[2/2] Simulating YES+NO PMM (Two-Sided Market Making)...")
    yn_sim = YES_NO_Simulator(capital=500.0, order_size=15.0, fee_bps=20.0)
    for tick in range(len(ticks_data)):
        yn_sim.run_tick(ticks_data[tick][1], tick)
        # Stop at 1000 completed paired cycles
        if yn_sim.completed_cycles >= 1000:
            break

    # 4. Report & Analysis
    def calculate_results(sim, is_yn=False):
        total_wins = sum(p for p in sim.round_trip_pnls if p > 0)
        total_losses = sum(p for p in sim.round_trip_pnls if p < 0)
        profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
        win_rate = (len([p for p in sim.round_trip_pnls if p > 0]) / len(sim.round_trip_pnls) * 100) if len(sim.round_trip_pnls) > 0 else 0.0
        ev_per_cycle = sim.realized_pnl / sim.completed_cycles if sim.completed_cycles > 0 else 0.0
        
        # Capital turnover: volume of capital recycled / capital pool size
        turnover_rate = sim.capital_recycled_volume / sim.capital
        
        # Monthly profit projection (pro-rated based on average ticks to hit 1000 cycles)
        # Assuming 1 tick = 10s polling. 1 day = 8640 ticks. 30 days = 259,200 ticks.
        projected_monthly = (sim.realized_pnl / tick) * 259200 if tick > 0 else 0.0
        
        return {
            "ev": ev_per_cycle,
            "pf": profit_factor,
            "pnl": sim.realized_pnl,
            "dd": sim.max_drawdown,
            "win_rate": win_rate,
            "turnover": turnover_rate,
            "monthly": projected_monthly,
            "ticks": tick
        }

    dir_res = calculate_results(dir_sim)
    yn_res = calculate_results(yn_sim, is_yn=True)
    
    leg_risk_freq = (yn_sim.leg_risk_count / yn_sim.completed_cycles * 100) if yn_sim.completed_cycles > 0 else 0.0
    hedge_freq = (yn_sim.hedge_count / yn_sim.completed_cycles * 100) if yn_sim.completed_cycles > 0 else 0.0
    failed_hedge_freq = (yn_sim.failed_hedge_count / yn_sim.completed_cycles * 100) if yn_sim.completed_cycles > 0 else 0.0
    avg_spread = (sum(yn_sim.spreads_captured) / len(yn_sim.spreads_captured) * 100) if len(yn_sim.spreads_captured) > 0 else 0.0
    
    print("\n" + "="*85)
    print("          YES+NO MARKET MAKING VS CURRENT PMM PERFORMANCE SIMULATION")
    print("="*85)
    print(f"{'Metric':<30} | {'CURRENT PMM (Sniper)':<22} | {'YES+NO PMM (Two-Sided)':<22}")
    print("-" * 85)
    print(f"{'1. EV per Paired Cycle ($)':<30} | {dir_res['ev']:+0.5f} {'':<14} | {yn_res['ev']:+0.5f}")
    print(f"{'2. Profit Factor':<30} | {dir_res['pf']:0.5f} {'':<14} | {yn_res['pf']:0.5f}")
    print(f"{'3. Realized PnL ($)':<30} | {dir_res['pnl']:+0.4f} {'':<14} | {yn_res['pnl']:+0.4f}")
    print(f"{'4. Maximum Drawdown ($)':<30} | {dir_res['dd']:.4f} {'':<14} | {yn_res['dd']:.4f}")
    print(f"{'5. Win Rate (%)':<30} | {dir_res['win_rate']:0.2f}% {'':<15} | {yn_res['win_rate']:0.2f}%")
    print(f"{'6. Capital Turnover Rate':<30} | {dir_res['turnover']:0.1f}x {'':<16} | {yn_res['turnover']:0.1f}x")
    print(f"{'7. Expected Monthly Profit':<30} | ${dir_res['monthly']:0.2f} {'':<14} | ${yn_res['monthly']:0.2f}")
    print(f"{'8. Completed Paired Cycles':<30} | {dir_sim.completed_cycles:<22} | {yn_sim.completed_cycles}")
    print(f"{'9. Simulation Ticks Run':<30} | {dir_res['ticks']:<22} | {yn_res['ticks']}")
    print("-" * 85)
    print("YES+NO STRATEGY PROCESS STATS:")
    print(f"  * Leg-risk frequency    : {leg_risk_freq:.2f}% ({yn_sim.leg_risk_count} times)")
    print(f"  * Active hedge frequency: {hedge_freq:.2f}% ({yn_sim.hedge_count} times)")
    print(f"  * Failed hedge frequency: {failed_hedge_freq:.2f}% ({yn_sim.failed_hedge_count} times)")
    print(f"  * Avg. spread captured  : {avg_spread:.3f}%")
    print("="*85)
    
    # Save validation results
    results = {
        "directional": dir_res,
        "yes_no": yn_res,
        "process_stats": {
            "leg_risk_freq": leg_risk_freq,
            "hedge_freq": hedge_freq,
            "failed_hedge_freq": failed_hedge_freq,
            "avg_spread_captured": avg_spread
        }
    }
    with open("storage/validation_yes_no_vs_current.json", "w") as fh:
        json.dump(results, fh, indent=2)

if __name__ == "__main__":
    asyncio.run(main())

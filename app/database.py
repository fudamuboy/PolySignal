import sqlite3
import os
import contextlib
from .config import DATABASE_URL, DB_PATH
from .logger import logger

class Database:
    _pool = None

    def __init__(self):
        self.db_path = DB_PATH
        self.is_postgresql = "postgresql" in DATABASE_URL
        if self.is_postgresql:
            try:
                import psycopg2
                import psycopg2.pool
            except ImportError:
                logger.warning("PostgreSQL driver psycopg2 not found. Falling back to SQLite.")
                self.is_postgresql = False
        self._init_db()

    def _init_pool(self):
        if self.is_postgresql and Database._pool is None:
            import psycopg2.pool
            logger.info("Initializing PostgreSQL ThreadedConnectionPool")
            Database._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=DATABASE_URL
            )

    @contextlib.contextmanager
    def _get_connection(self):
        if self.is_postgresql:
            self._init_pool()
            conn = Database._pool.getconn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                Database._pool.putconn(conn)
        else:
            conn = sqlite3.connect(self.db_path)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _format_query(self, query):
        if self.is_postgresql:
            # Replace SQLite param placeholder '?' with PostgreSQL '%s'
            return query.replace("?", "%s")
        return query

    def _format_create_table(self, query):
        if self.is_postgresql:
            q = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            q = q.replace("DATETIME DEFAULT CURRENT_TIMESTAMP", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            return q
        return query

    def _init_db(self):
        """Initialize database schema if it doesn't exist."""
        if not self.is_postgresql:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Trades table
            cursor.execute(self._format_create_table('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size REAL NOT NULL,
                    price REAL NOT NULL,
                    strategy TEXT DEFAULT 'Unknown',
                    spread REAL DEFAULT 0,
                    slippage REAL DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    realized_pnl REAL DEFAULT 0
                )
            '''))
            # Positions table
            cursor.execute(self._format_create_table('''
                CREATE TABLE IF NOT EXISTS positions (
                    token_id TEXT PRIMARY KEY,
                    size REAL NOT NULL,
                    avg_price REAL NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            
            # Inventory history table
            cursor.execute(self._format_create_table('''
                CREATE TABLE IF NOT EXISTS inventory_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_exposure REAL NOT NULL,
                    max_position_exposure REAL NOT NULL,
                    concentration REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            
            # Migration: Add new columns if not exists
            if self.is_postgresql:
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'trades'")
                columns = [row[0] for row in cursor.fetchall()]
            else:
                cursor.execute("PRAGMA table_info(trades)")
                columns = [column[1] for column in cursor.fetchall()]

            if 'strategy' not in columns:
                logger.info("Database Migration: Adding 'strategy' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'Unknown'")
            if 'spread' not in columns:
                logger.info("Database Migration: Adding 'spread' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN spread REAL DEFAULT 0")
            if 'slippage' not in columns:
                logger.info("Database Migration: Adding 'slippage' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN slippage REAL DEFAULT 0")
            if 'fee_paid' not in columns:
                logger.info("Database Migration: Adding 'fee_paid' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN fee_paid REAL DEFAULT 0")
            if 'order_id' not in columns:
                logger.info("Database Migration: Adding 'order_id' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN order_id TEXT DEFAULT NULL")
            if 'fill_price' not in columns:
                logger.info("Database Migration: Adding 'fill_price' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN fill_price REAL DEFAULT NULL")
            if 'is_maker' not in columns:
                logger.info("Database Migration: Adding 'is_maker' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN is_maker INTEGER DEFAULT 1")
            if 'market_id' not in columns:
                logger.info("Database Migration: Adding 'market_id' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN market_id TEXT DEFAULT NULL")
            if 'exit_reason' not in columns:
                logger.info("Database Migration: Adding 'exit_reason' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT DEFAULT NULL")
            if 'parent_trade_id' not in columns:
                logger.info("Database Migration: Adding 'parent_trade_id' column to 'trades' table")
                cursor.execute("ALTER TABLE trades ADD COLUMN parent_trade_id INTEGER DEFAULT NULL")

            # Positions remember their opening strategy so managed inventory (e.g. market-making
            # pairs) is not handed to the generic SL/TP exit engine after a restart
            if self.is_postgresql:
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'positions'")
                position_columns = [row[0] for row in cursor.fetchall()]
            else:
                cursor.execute("PRAGMA table_info(positions)")
                position_columns = [column[1] for column in cursor.fetchall()]
            if 'strategy' not in position_columns:
                logger.info("Database Migration: Adding 'strategy' column to 'positions' table")
                cursor.execute("ALTER TABLE positions ADD COLUMN strategy TEXT DEFAULT 'Unknown'")
                
            conn.commit()

    def save_inventory_snapshot(self, total_exposure, max_position_exposure, concentration):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._format_query('''
                INSERT INTO inventory_history (total_exposure, max_position_exposure, concentration)
                VALUES (?, ?, ?)
            '''), (total_exposure, max_position_exposure, concentration))
            conn.commit()

    def save_trade(self, token_id, side, size, price, strategy='Unknown', realized_pnl=0, spread=0, slippage=0,
                   fee_paid=0.0, order_id=None, fill_price=None, is_maker=1, market_id=None, exit_reason=None, parent_trade_id=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._format_query('''
                INSERT INTO trades (
                    token_id, side, size, price, strategy, realized_pnl, spread, slippage,
                    fee_paid, order_id, fill_price, is_maker, market_id, exit_reason, parent_trade_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''), (
                str(token_id), str(side), float(size), float(price), str(strategy),
                float(realized_pnl), float(spread), float(slippage), float(fee_paid),
                str(order_id) if order_id else None,
                float(fill_price) if fill_price is not None else float(price),
                int(is_maker),
                str(market_id) if market_id else None,
                str(exit_reason) if exit_reason else None,
                int(parent_trade_id) if parent_trade_id is not None else None
            ))
            conn.commit()

    def update_position(self, token_id, size, avg_price, strategy='Unknown'):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if size <= 0:
                cursor.execute(self._format_query('DELETE FROM positions WHERE token_id = ?'), (token_id,))
            else:
                if self.is_postgresql:
                    # PostgreSQL UPSERT
                    cursor.execute(self._format_query('''
                        INSERT INTO positions (token_id, size, avg_price, strategy, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(token_id) DO UPDATE SET
                            size = EXCLUDED.size,
                            avg_price = EXCLUDED.avg_price,
                            strategy = EXCLUDED.strategy,
                            updated_at = CURRENT_TIMESTAMP
                    '''), (token_id, size, avg_price, strategy))
                else:
                    # SQLite UPSERT
                    cursor.execute('''
                        INSERT INTO positions (token_id, size, avg_price, strategy, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(token_id) DO UPDATE SET
                            size = excluded.size,
                            avg_price = excluded.avg_price,
                            strategy = excluded.strategy,
                            updated_at = CURRENT_TIMESTAMP
                    ''', (token_id, size, avg_price, strategy))
            conn.commit()

    def load_positions(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT token_id, size, avg_price, strategy FROM positions')
            rows = cursor.fetchall()
            return {row[0]: {'size': row[1], 'avg_price': row[2], 'strategy': row[3] or 'Unknown'} for row in rows}

    def clear_all_positions(self):
        """Delete all positions from the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM positions')
            conn.commit()
            logger.info("Cleared all positions from database")

    def get_trade_history(self, limit=50):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._format_query('SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?'), (limit,))
            return cursor.fetchall()

    def get_performance_metrics(self):
        """Fetch overall performance metrics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Overall PnL, Win Rate, Avg Profit/Loss
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(realized_pnl) as total_pnl,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE NULL END) as avg_profit,
                    AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE NULL END) as avg_loss
                FROM trades
                WHERE realized_pnl != 0
            ''')
            overall = cursor.fetchone()
            
            # Per-strategy breakdown
            cursor.execute('''
                SELECT 
                    strategy,
                    COUNT(*) as trades,
                    SUM(realized_pnl) as pnl,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
                FROM trades
                WHERE realized_pnl != 0
                GROUP BY strategy
            ''')
            strategy_breakdown = cursor.fetchall()
            
            return {
                "overall": overall,
                "strategies": strategy_breakdown
            }

    def get_engine_performance(self):
        """Fetch comprehensive performance metrics for all strategies including fees and attribution."""
        with self._get_connection() as conn:
            if self.is_postgresql:
                import psycopg2.extras
                cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            else:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
            
            # Fetch all strategies with completed trades (realized_pnl != 0 or side == 'SELL')
            cursor.execute('''
                SELECT 
                    strategy,
                    COUNT(*) as total_trades,
                    SUM(realized_pnl) as net_pnl,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                    AVG(size * price) as avg_trade_size,
                    AVG(realized_pnl) as avg_trade_pnl,
                    SUM(fee_paid) as total_fees
                FROM trades
                WHERE realized_pnl != 0
                GROUP BY strategy
            ''')
            rows = cursor.fetchall()
            
            performance = {}
            for row in rows:
                strat = row['strategy']
                total = row['total_trades']
                net_pnl = float(row['net_pnl'] or 0.0)
                wins = int(row['wins'] or 0)
                losses = int(row['losses'] or 0)
                avg_size = float(row['avg_trade_size'] or 0.0)
                avg_pnl = float(row['avg_trade_pnl'] or 0.0)
                total_fees = float(row['total_fees'] or 0.0)
                
                # Win rate
                win_rate = (wins / total * 100.0) if total > 0 else 0.0
                
                # EV (average realized PnL per trade)
                ev = (net_pnl / total) if total > 0 else 0.0
                
                # Fetch detailed trades to calculate profit factor and drawdown
                if self.is_postgresql:
                    import psycopg2.extras
                    inner_cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                else:
                    inner_cursor = conn.cursor()
                
                inner_cursor.execute(self._format_query('''
                    SELECT realized_pnl 
                    FROM trades 
                    WHERE strategy = ? AND realized_pnl != 0
                    ORDER BY timestamp ASC
                '''), (strat,))
                trade_pnls = [r[0] for r in inner_cursor.fetchall()]
                inner_cursor.close()
                
                # Profit factor: sum(wins) / abs(sum(losses))
                gross_profits = sum(p for p in trade_pnls if p > 0)
                gross_losses = sum(p for p in trade_pnls if p < 0)
                profit_factor = (gross_profits / abs(gross_losses)) if gross_losses != 0 else (gross_profits if gross_profits > 0 else 1.0)
                
                # Maximum Drawdown calculation
                cum_pnl = 0.0
                peak = 0.0
                max_dd = 0.0
                for pnl in trade_pnls:
                    cum_pnl += pnl
                    if cum_pnl > peak:
                        peak = cum_pnl
                    dd = peak - cum_pnl
                    if dd > max_dd:
                        max_dd = dd
                        
                performance[strat] = {
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses,
                    "realized_pnl": round(net_pnl, 4),
                    "win_rate": round(win_rate, 2),
                    "ev": round(ev, 4),
                    "avg_trade_size": round(avg_size, 2),
                    "avg_trade_pnl": round(avg_pnl, 4),
                    "total_fees": round(total_fees, 4),
                    "profit_factor": round(profit_factor, 2),
                    "max_drawdown": round(max_dd, 4)
                }
                
            return performance

import sys
import ccxt
import threading
from threading import Event

from pprint import pprint

import pandas as pd
from configparser import ConfigParser

# Read Settings CSV
df = pd.read_csv('sl_settings.csv')

# Read Key CSV
key_df = pd.read_csv('keys.csv')

# Read proxies CSV
proxy_df = pd.read_csv('proxies.csv')

# Read master settings
master_config = ConfigParser()
master_config.read("master_settings.ini")

# Read Adjuster Settings
adj_config = ConfigParser()
adj_config.read("sl_adjuster_settings.ini")

fast_mode_delay = int(adj_config['timing']['fast_mode_delay'])
slow_mode_delay = int(adj_config['timing']['slow_mode_delay'])

exit_event = Event()

fast_bots = set()


def set_sl(bot_id, pair, side, sl):
    try:
        # Read bot settings
        config = ConfigParser()
        config.read(f"bots/{bot_id}.ini")

        # Read Key/Secret Row
        key_row = key_df.loc[key_df['botid'] == bot_id]
        if len(key_row) != 1:
            raise Exception(f"FATAL ERROR: bot {bot_id} has no auth in csv.")

        bot_key = key_row['key'].values[0]
        bot_secret = key_row['secret'].values[0]

        bybit = ccxt.bybit({
            'apiKey': bot_key,
            'secret': bot_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True
            }
        })

        if 'testnet' in master_config['main'] and master_config['main']['testnet'] == 'true':
            print(f"Operating in sandbox mode.")
            bybit.set_sandbox_mode(True)

        # Read Proxy Row
        proxy_row = proxy_df.loc[proxy_df['botid'] == bot_id]
        if len(proxy_row) == 1:
            url = proxy_row['url'].values[0]
            print(f"Using proxy: {url}")
            bybit.proxies = {
                'https': url
            }

        print(f"Loading market data...")
        markets = bybit.load_markets()
        market = bybit.market(pair)
        symbol = market['id']

        bybit.private_linear_post_position_trading_stop({"symbol": symbol,
                                                         "side": side,
                                                         "stop_loss": round(sl, 2)})
        return True

    except Exception as e:
        print(e)
        return False


def get_position(bot_id, pair):
    try:
        # Read bot settings
        config = ConfigParser()
        config.read(f"bots/{bot_id}.ini")

        # Read Key/Secret Row
        key_row = key_df.loc[key_df['botid'] == bot_id]
        if len(key_row) != 1:
            raise Exception(f"FATAL ERROR: bot {bot_id} has no auth in csv.")

        print(key_row)
        bot_key = key_row['key'].values[0]
        bot_secret = key_row['secret'].values[0]

        bybit = ccxt.bybit({
            'apiKey': bot_key,
            'secret': bot_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True
            }
        })

        if 'testnet' in master_config['main'] and master_config['main']['testnet'] == 'true':
            print(f"Operating in sandbox mode.")
            bybit.set_sandbox_mode(True)

        # Read Proxy Row
        proxy_row = proxy_df.loc[proxy_df['botid'] == bot_id]
        if len(proxy_row) == 1:
            url = proxy_row['url'].values[0]
            print(f"Using proxy: {url}")
            bybit.proxies = {
                'https': url
            }

        print(f"Loading market data...")
        markets = bybit.load_markets()
        market = bybit.market(pair)
        symbol = market['id']

        # Get current price
        response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
        index_price = float(response['result'][0]['price'])

        # Check for current positions
        response = bybit.fetch_positions(symbols=[pair])

        have_buy_position = False
        have_sell_position = False
        buy_qty = None
        sell_qty = None
        buy_sl = None
        sell_sl = None
        buy_upnl = None
        sell_upnl = None
        buy_entry = None
        sell_entry = None
        buy_leverage = None
        sell_leverage = None

        if len(response) != 2:
            raise Exception("error getting active positions")

        for p in response:
            if p['side'] == "Sell" and float(p['size']) != 0.0:
                have_sell_position = True
                sell_qty = float(p['size'])
                sell_sl = float(p['stop_loss'])
                sell_upnl = float(p['unrealised_pnl'])
                sell_entry = float(p['entry_price'])
                sell_leverage = float(p['leverage'])

                # response = bybit.fetch_ticker(pair)
                # index_price = float(response['high'])

            if p['side'] == "Buy" and float(p['size']) != 0.0:
                have_buy_position = True
                buy_qty = float(p['size'])
                buy_sl = float(p['stop_loss'])
                buy_upnl = float(p['unrealised_pnl'])
                buy_entry = float(p['entry_price'])
                buy_leverage = float(p['leverage'])

                # response = bybit.fetch_ticker(pair)
                # index_price = float(response['high'])

        if not have_buy_position and not have_sell_position:
            return False, 'no', 0, 0, 0, 0, 0
        elif have_buy_position and not have_sell_position:
            return True, 'long', buy_qty, buy_sl, buy_upnl, buy_entry, index_price
        elif not have_buy_position and have_sell_position:
            return True, 'short', sell_qty, sell_sl, sell_upnl, sell_entry, index_price
        else:
            raise Exception("impossible !")

    except Exception as e:
        print(e)
        return False, 'no', 0, 0, 0, 0, 0


def slow_loop():
    while not exit_event.is_set():
        for i, row in df.iterrows():
            bot_id = row['botid']
            pair = row['pair']
            iden = f"{bot_id}_{pair}"
            if iden in fast_bots:
                continue
            print(f"[SLOW LOOP] Checking {bot_id} for {pair} positions.")
            has_p, p_type, p_qty, p_sl, p_upnl, p_entry, p_cur_price = get_position(bot_id, pair)
            if has_p:
                # Move Bot to fast loop
                fast_bots.add(iden)
                print(f"Bot {bot_id} added to fast bots.")

                pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                if p_type == "short":
                    pnl_percent *= -1
                print(f"PNL percent: {pnl_percent}")

                best_match_a = None
                best_match_b = None
                # loop in columns
                c = 1
                while f"{c}a" in row and f"{c}b":
                    a_v = row[f"{c}a"]
                    b_v = row[f"{c}b"]
                    if pd.isna(a_v) or pd.isna(b_v):
                        break
                    if pnl_percent > a_v:
                        best_match_a = a_v
                        best_match_b = b_v
                    c += 1
                # best match
                if best_match_a is not None and best_match_b is not None:
                    print(f"Best A value: {best_match_a}  Best B Value: {best_match_b}")
                    if p_type == "long":
                        best_match_b *= -1
                    proposed_sl = p_entry * (1 + (best_match_b / 100))

                    print(f"entry: {p_entry}  proposed sl: {proposed_sl} current sl: {p_sl}")
                    if p_type == "long" and (p_sl == 0 or proposed_sl > p_sl):
                        print("Updating Stop Loss for long position...")
                        r = set_sl(bot_id, pair, "Buy", proposed_sl)
                        if r:
                            print("Stop loss updated!")
                        else:
                            print("error occurred on stop loss set.")
                    elif p_type == "short" and (p_sl == 0 or proposed_sl < p_sl):
                        print("Updating Stop Loss for short position...")
                        r = set_sl(bot_id, pair, "Sell", proposed_sl)
                        if r:
                            print("Stop loss updated!")
                        else:
                            print("error occurred on stop loss set.")
                    else:
                        print("no need to change stop loss.")

        exit_event.wait(slow_mode_delay)


def fast_loop():
    while not exit_event.is_set():
        print(f"fast bots: {fast_bots}")
        for i, row in df.iterrows():
            bot_id = row['botid']
            pair = row['pair']
            iden = f"{bot_id}_{pair}"
            if iden not in fast_bots:
                continue
            print(f"[FAST LOOP] checking {bot_id} for {pair} positions.")
            has_p, p_type, p_qty, p_sl, p_upnl, p_entry, p_cur_price = get_position(bot_id, pair)
            if has_p:

                pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                if p_type == "short":
                    pnl_percent *= -1
                print(f"PNL percent: {pnl_percent}")

                best_match_a = None
                best_match_b = None
                # loop in columns
                c = 1
                while f"{c}a" in row and f"{c}b":
                    a_v = row[f"{c}a"]
                    b_v = row[f"{c}b"]
                    if pd.isna(a_v) or pd.isna(b_v):
                        break
                    if pnl_percent > a_v:
                        best_match_a = a_v
                        best_match_b = b_v
                    c += 1
                # best match
                print(f"Best A value: {best_match_a}  Best B Value: {best_match_b}")
                if best_match_a is not None and best_match_b is not None:
                    if p_type == "long":
                        best_match_b *= -1
                    proposed_sl = p_entry * (1 + (best_match_b / 100))

                    print(f"entry: {p_entry}  proposed sl: {proposed_sl} current sl: {p_sl}")
                    if p_type == "long" and (p_sl == 0 or proposed_sl > p_sl):
                        print("Updating Stop Loss for long position...")
                        r = set_sl(bot_id, pair, "Buy", proposed_sl)
                        if r:
                            print("Stop loss updated!")
                        else:
                            print("error occurred on stop loss set.")
                    elif p_type == "short" and (p_sl == 0 or proposed_sl < p_sl):
                        print("Updating Stop Loss for short position...")
                        r = set_sl(bot_id, pair, "Sell", proposed_sl)
                        if r:
                            print("Stop loss updated!")
                        else:
                            print("error occurred on stop loss set.")
                    else:
                        print("no need to change stop loss.")
            else:
                # Remove from fast loop
                print(f"Removing Bot {bot_id} from fast bots.")
                fast_bots.discard(iden)

        exit_event.wait(fast_mode_delay)


def service_quit(signo, _frame):
    print(f"Interrupted by {signo}, shutting down...")
    exit_event.set()


if __name__ == '__main__':
    print(df.to_string())

    # Handle termination signals
    import signal

    for sig in ('TERM', 'INT'):
        signal.signal(getattr(signal, 'SIG' + sig), service_quit)

    slow = threading.Thread(target=slow_loop)
    fast = threading.Thread(target=fast_loop)

    print("Starting Slow Loop...")
    slow.start()
    print("Starting Fast Loop...")
    fast.start()

    slow.join()
    fast.join()

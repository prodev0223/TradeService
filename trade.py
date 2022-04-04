import sys
import json
import time
from datetime import datetime, timedelta
import ccxt
from ccxt import ExchangeError
from pprint import pprint
import pandas as pd

from mongoengine import *
from configparser import ConfigParser

# MongoEngine Schema
class Message(Document):
    bot_id = StringField(required=True)
    pair = StringField(required=True)
    command = StringField(required=True)
    percent = StringField(default="none")
    timestamp = DateTimeField(default=datetime.utcnow)
    status = StringField(default="pending")
    error_msg = StringField()
    error_severity = StringField()


class Lock(Document):
    bot_id = StringField(required=True)


def get_error_json(e: ExchangeError):
    feedback = str(e.args[0])
    return json.loads(feedback[feedback.find("{"):])


def log_error(msg, text, severity):
    if severity == "warn":
        cprint(f"ERROR: {text}", BColors.WARNING)
    else:
        severity = "high"
        cprint(f"ERROR: {text}", BColors.FAIL)
    msg.error_msg = text
    msg.error_severity = severity
    msg.status = "failed"
    msg.save()


def log_success(msg, text):
    cprint(text, BColors.OKGREEN)
    msg.status = "success"
    msg.save()


def release_lock(lock_id):
    lock = Lock.objects(bot_id=lock_id).first()
    if lock is None:
        print("Process was initiated without locking. this is NOT recommended, "
              "make sure you are running the script via QueueService!")
    else:
        if verbose:
            print("Releasing lock...")
        lock.delete()


def do_with_retry(func, *args):
    number_of_tries = 3
    current_try = 0
    timeout = 5
    last_e = None
    while current_try < number_of_tries:
        try:
            # try to do it
            func(*args)
            return
        except Exception as e:
            last_e = e
            current_try += 1
            print(f"Trying again in {timeout} seconds.")
            time.sleep(timeout)
    raise last_e


class BColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def cprint(text, color):
    print(color + text + BColors.ENDC)


# def critical_error_handler():
#     # When critical error happens all of the messages get failed
#     objs = Message.objects(bot_id=bot_id, status="pending")
def get_position(bybit, bot_id, pair):
    try:
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
            side = p['side']
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
            return False, 'no', 0, 0, 0, 0, 0, 'no'
        elif have_buy_position and not have_sell_position:
            return True, 'long', buy_qty, buy_sl, buy_upnl, buy_entry, index_price, side
        elif not have_buy_position and have_sell_position:
            return True, 'short', sell_qty, sell_sl, sell_upnl, sell_entry, index_price, side
        else:
            raise Exception("impossible !")

    except Exception as e:
        print(e)
        return False, 'no', 0, 0, 0, 0, 0

if __name__ == '__main__':
    bot_id = sys.argv[1]

    verbose = True
    if len(sys.argv) == 3 and sys.argv[2] == "-silent":
        verbose = False
    verbose = True
    if verbose:
        cprint(f"BotID: {bot_id}", BColors.OKBLUE)

    config = ConfigParser()
    config.read(f"bots/{bot_id}.ini")

    # Connect to DB
    connect('trade_db')
    if verbose:
        print("Message database connected!")

    # Read Key CSV
    key_df = pd.read_csv('keys.csv')

    # Read Key/Secret Row
    key_row = key_df.loc[key_df['botid'] == int(bot_id)]
    if len(key_row) != 1:
        cprint(f"ERROR: no auth for bot {bot_id} in csv. whole service will die.", BColors.FAIL)
        release_lock(bot_id)
        sys.exit(-1)

    bot_key = key_row['key'].values[0]
    bot_secret = key_row['secret'].values[0]

    # print(f"Key: {bot_key} , Secret: {bot_secret}")

    bybit = ccxt.bybit({
        'apiKey': bot_key,
        'secret': bot_secret,
        'enableRateLimit': True,
        'options': {
            'adjustForTimeDifference': True
        }
    })

    # Read proxies CSV
    proxy_df = pd.read_csv('proxies.csv')

    # Read Proxy Row
    proxy_row = proxy_df.loc[proxy_df['botid'] == int(bot_id)]
    if len(proxy_row) == 1:
        url = proxy_row['url'].values[0]
        if verbose:
            print(f"Using proxy: {url}")
        bybit.proxies = {
            'https': url
        }

    # Read master settings
    master_config = ConfigParser()
    master_config.read("master_settings.ini")
    if 'testnet' in master_config['main'] and master_config['main']['testnet'] == 'true':
        if verbose:
            print(f"Operating in sandbox mode.")
        bybit.set_sandbox_mode(True)

    max_webhook_message_age_time = 90
    max_order_time = 60
    if 'timing' in config.sections() and 'max_webhook_message_age_time' in config['timing']:
        max_webhook_message_age_time = int(config['timing']['max_webhook_message_age_time'])
    if 'timing' in config.sections() and 'max_order_time' in config['timing']:
        max_order_time = int(config['timing']['max_order_time'])

    objs = Message.objects(bot_id=bot_id, status="pending").order_by('+timestamp')
    if len(objs) == 0:
        if verbose:
            print("There are no messages to process. exiting...")
        release_lock(bot_id)
        sys.exit(-1)

    if verbose:
        print(f"Loading market data...")
    markets = bybit.load_markets()

    for i, msg in enumerate(objs):
        if verbose:
            print("---------------------------")
            print(f"Processing message {i + 1}/{len(objs)} for this bot.")
            print(f"Timestamp: {msg.timestamp}")
        pair = msg.pair.upper()
        command = msg.command.lower()
        print(f"command={command}")
        start_time = datetime.utcnow()

        try:
            # Check message expire
            if (datetime.utcnow() - msg.timestamp).seconds >= max_webhook_message_age_time:
                raise Exception(f"max_webhook_message_age_time expired for message. ({max_webhook_message_age_time}s)",
                                "warn")

            market = bybit.market(pair)
            symbol = market['id']
            base = market['base']

            if command == "enter-short":
                if verbose:
                    print(f"Entering short position in {pair}")

                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                if len(response) != 2:
                    raise Exception("error getting active positions")

                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                if have_buy_position or have_sell_position:
                    raise Exception("this bot already has a position open.", "warn")
                else:
                    # Set Leverage Value
                    leverage = float(config['trade'][f"{pair}_leverage_multiple"])
                    is_isolated = config['trade'][f"{pair}_is_isolated"] == "true"

                    if verbose:
                        print("Setting Cross/Isolated...")
                        print(f"is_isolated value: {is_isolated}")

                    try:
                        response = bybit.private_linear_post_position_switch_isolated({"symbol": symbol,
                                                                                       "is_isolated": is_isolated,
                                                                                       "buy_leverage": leverage,
                                                                                       "sell_leverage": leverage})
                    except ExchangeError as e:
                        err_json = get_error_json(e)
                        if err_json['ret_code'] == 130056:
                            if verbose:
                                print("Cross/Isolated already at the desired value.")
                        else:
                            raise Exception("error setting Cross/Isolated.")

                    if verbose:
                        print(f"Leverage value: {leverage}")
                        print(f"Setting leverage...")
                    try:
                        response = bybit.private_linear_post_position_set_leverage({"symbol": symbol,
                                                                                    "buy_leverage": leverage,
                                                                                    "sell_leverage": leverage})
                    except ExchangeError as e:
                        err_json = get_error_json(e)
                        if err_json['ret_code'] == 34036:
                            if verbose:
                                print("Leverage already at the desired value.")
                        else:
                            raise Exception("error setting leverage.")

                    # Get Portfolio Value
                    if verbose:
                        print("Getting portfolio...")
                    response = bybit.fetch_balance()
                    usdt_portfolio = float(response['USDT']['free'])
                    if verbose:
                        print(f"Available USDT Portfolio Value: {usdt_portfolio}")
                    invest_precent = float(config['trade'][f"{pair}_portfolio_percent"])
                    if verbose:
                        print(f"Portfolio percentage: {invest_precent}%")

                    # Get Latest price for symbol
                    response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                    index_price = float(response['result'][0]['price'])
                    if verbose:
                        print(f"Price for {base}: {index_price}")

                    # Calculate Qty
                    position_qty = round((usdt_portfolio * leverage * (invest_precent / 100)) / index_price, 2)
                    if verbose:
                        print(f"Position qty: {position_qty}")

                    # Check max_order_time
                    if (datetime.utcnow() - start_time).seconds >= max_order_time:
                        raise Exception("max_order_time expired.", "warn")

                    # response = bybit.create_order(pair, "Market", "Sell", position_qty)

                    # Set Stop Loss
                    sl_price = None
                    if f"{pair}_stop_loss" in config['trade']:
                        sl_percent = float(config['trade'][f"{pair}_stop_loss"])
                        sl_price = round(((100 + sl_percent) / 100) * index_price, 2)  # in short its bigger than entry
                        if verbose:
                            print(f"SL Price: {sl_price}")
                    
                    if sl_price is not None:
                        response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Sell",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False,
                                                                           "stop_loss": sl_price})
                    else:
                        response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Sell",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False})

                    # Read Take Profit Setting
                    tps = []
                    tpc = 1
                    total_percentage = 0.0
                    while f"{pair}_tp_{tpc}_%" in config['trade']:
                        tp_percent = float(config['trade'][f"{pair}_tp_{tpc}_%"])
                        tp_percent_of_position = float(config['trade'][f"{pair}_tp_{tpc}_%_of_position"])
                        total_percentage += tp_percent_of_position
                        tps.append((tp_percent, tp_percent_of_position))
                        tpc += 1

                    if len(tps) > 0 and total_percentage != 100.0:
                        raise Exception("sum of take profit percents should be 100.")

                    for tp in tps:
                        tp_qty = (tp[1] / 100) * position_qty
                        tp_price = round(((100 - tp[0]) / 100) * index_price, 2)  # for short its less than entry
                        if verbose:
                            print("Setting Take Profit...")
                            print(f"TP Qty : {tp_qty}   TP Price: {tp_price}")
                        response = bybit.privateLinearPostStopOrderCreate({"symbol": symbol,
                                                                           "side": "Buy",
                                                                           "order_type": "Market",
                                                                           "qty": tp_qty,
                                                                           "base_price": index_price,
                                                                           "stop_px": tp_price,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "trigger_by": "LastPrice",
                                                                           "close_on_trigger": True,
                                                                           "reduce_only": True
                                                                           })

                    log_success(msg, "Position opened successfully.")

            elif command == "enter-long":
                if verbose:
                    print(f"Entering long position in {pair}")

                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                if len(response) != 2:
                    raise Exception("error getting active positions")

                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                if have_buy_position or have_sell_position:
                    raise Exception("this bot already has a position open.", "warn")
                else:

                    # Set Leverage Value
                    leverage = float(config['trade'][f"{pair}_leverage_multiple"])
                    is_isolated = config['trade'][f"{pair}_is_isolated"] == "true"

                    if verbose:
                        print("Setting Cross/Isolated...")
                        print(f"is_isolated value: {is_isolated}")

                    try:
                        response = bybit.private_linear_post_position_switch_isolated({"symbol": symbol,
                                                                                       "is_isolated": is_isolated,
                                                                                       "buy_leverage": leverage,
                                                                                       "sell_leverage": leverage})
                    except ExchangeError as e:
                        err_json = get_error_json(e)
                        if err_json['ret_code'] == 130056:
                            if verbose:
                                print("Cross/Isolated already at the desired value.")
                        else:
                            raise Exception("error setting Cross/Isolated.")

                    # Set Leverage Value
                    if verbose:
                        print(f"Leverage value: {leverage}")
                        print(f"Setting leverage...")
                    try:
                        response = bybit.private_linear_post_position_set_leverage({"symbol": symbol,
                                                                                    "buy_leverage": leverage,
                                                                                    "sell_leverage": leverage})
                    except ExchangeError as e:
                        err_json = get_error_json(e)
                        if err_json['ret_code'] == 34036:
                            if verbose:
                                print("Leverage already at the desired value.")
                        else:
                            raise Exception("error setting leverage.")

                    # Get Portfolio Value
                    if verbose:
                        print("Getting portfolio...")
                    response = bybit.fetch_balance()
                    usdt_portfolio = float(response['USDT']['free'])
                    if verbose:
                        print(f"Available USDT Portfolio Value: {usdt_portfolio}")
                    invest_precent = float(config['trade'][f"{pair}_portfolio_percent"])
                    if verbose:
                        print(f"Portfolio percentage: {invest_precent}%")

                    # Get Latest price for symbol
                    response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                    index_price = float(response['result'][0]['price'])
                    if verbose:
                        print(f"Price for {base}: {index_price}")

                    # Calculate Qty
                    position_qty = round((usdt_portfolio * leverage * (invest_precent / 100)) / index_price,
                                         2)
                    if verbose:
                        print(f"Position qty: {position_qty}")

                    # Check max_order_time
                    if (datetime.utcnow() - start_time).seconds >= max_order_time:
                        raise Exception("max_order_time expired.", "warn")

                    # response = bybit.create_order(pair, "Market", "Sell", position_qty)

                    # Set Stop Loss
                    sl_price = None
                    if f"{pair}_stop_loss" in config['trade']:
                        sl_percent = float(config['trade'][f"{pair}_stop_loss"])
                        sl_price = round(((100 - sl_percent) / 100) * index_price, 2)  # in long its smaller than entry
                        if verbose:
                            print(f"SL Price: {sl_price}")

                    if sl_price is not None:
                        response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Buy",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False,
                                                                           "stop_loss": sl_price})
                    else:
                        response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Buy",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False})

                    # Read Take Profit Setting
                    tps = []
                    tpc = 1
                    total_percentage = 0.0
                    while f"{pair}_tp_{tpc}_%" in config['trade']:
                        tp_percent = float(config['trade'][f"{pair}_tp_{tpc}_%"])
                        tp_percent_of_position = float(config['trade'][f"{pair}_tp_{tpc}_%_of_position"])
                        total_percentage += tp_percent_of_position
                        tps.append((tp_percent, tp_percent_of_position))
                        tpc += 1

                    if len(tps) > 0 and total_percentage != 100.0:
                        raise Exception("sum of take profit percents should be 100.")

                    for tp in tps:
                        tp_qty = (tp[1] / 100) * position_qty
                        tp_price = round(((100 + tp[0]) / 100) * index_price, 2)  # for long its more than entry
                        if verbose:
                            print("Setting Take Profit...")
                            print(f"TP Qty : {tp_qty}   TP Price: {tp_price}")
                        response = bybit.privateLinearPostStopOrderCreate({"symbol": symbol,
                                                                           "side": "Sell",
                                                                           "order_type": "Market",
                                                                           "qty": tp_qty,
                                                                           "base_price": index_price,
                                                                           "stop_px": tp_price,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "trigger_by": "LastPrice",
                                                                           "close_on_trigger": True,
                                                                           "reduce_only": True
                                                                           })

                    log_success(msg, "Position opened successfully.")

            elif command == "exit-short":
                if verbose:
                    print(f"Closing short position in {pair}")

                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")

                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                        buy_qty = float(p['size'])
                if have_buy_position:
                    raise Exception("this bot has a long position open.", "warn")

                elif have_sell_position:
                    # Cancel All Conditional Orders
                    bybit.private_linear_post_stop_order_cancel_all({"symbol": symbol})

                    # Close short position
                    response = bybit.create_order(pair, "Market", "Buy", sell_qty, params={
                        'reduce_only': True, 'close_on_trigger': True
                    })
                    if response['info']['order_status'] == "Created":
                        log_success(msg, "Position closed successfully.")
                else:
                    raise Exception("there is no active short position to close.", "warn")

            elif command == "exit-long":
                if verbose:
                    print(f"Closing long position in {pair}")

                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")

                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                        buy_qty = float(p['size'])
                if have_sell_position:
                    raise Exception("this bot has a short position open.", "warn")
                elif have_buy_position:
                    # Cancel All Conditional Orders
                    bybit.private_linear_post_stop_order_cancel_all({"symbol": symbol})

                    # Close short position
                    response = bybit.create_order(pair, "Market", "Sell", buy_qty, params={
                        'reduce_only': True, 'close_on_trigger': True
                    })
                    if response['info']['order_status'] == "Created":
                        log_success(msg, "Position closed successfully.")
                else:
                    raise Exception("there is no active long position to close.", "warn")
            
            elif command == "take-profit-long-1":
                if verbose:
                    print(f"take profit long1 in {pair}")
                response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                p_cur_price = float(response['result'][0]['price'])
                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")
                p_entry = 0
                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        p_entry = float(p['entry_price'])
                        have_buy_position = True
                        buy_qty = float(p['size'])
                if have_sell_position:
                    raise Exception("this bot has a short position open.", "warn")
                elif have_buy_position:
                    pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                    if pnl_percent>0:
                        # Close short position
                        response = bybit.create_order(pair, "Market", "Sell", buy_qty, params={
                            'reduce_only': True, 'close_on_trigger': True
                        })
                        if response['info']['order_status'] == "Created":
                            log_success(msg, "Position closed successfully.")
                    else:
                        log_error(msg, "profit is not positive", "warn")
                else:
                    raise Exception("there is no active long position to close.", "warn")
            elif command == "take-profit-short-1":
                if verbose:
                    print(f"take profit short1 in {pair}")
                response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                p_cur_price = float(response['result'][0]['price'])
                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")
                p_entry = 0
                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                        p_entry = float(p['entry_price'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                        buy_qty = float(p['size'])
                if have_buy_position:
                    raise Exception("this bot has a long position open.", "warn")

                elif have_sell_position:
                    pnl_percent = -((p_cur_price - p_entry)/p_entry)*100
                    print(f"PNL percent: {pnl_percent}")
                    if pnl_percent>0:
                        # Close short position
                        response = bybit.create_order(pair, "Market", "Buy", sell_qty, params={
                            'reduce_only': True, 'close_on_trigger': True
                        })
                        if response['info']['order_status'] == "Created":
                            log_success(msg, "Position closed successfully.")
                    else:
                        log_error(msg, "profit is not positive", "warn")
                else:
                    raise Exception("there is no active short position to close.", "warn")

            elif command == "take-profit-long-2":
                if verbose:
                    print(f"take profit long2 in {pair}")
                percent = msg.percent
                percent = percent[:-1]
                print(f"specific percent: {percent}")
                percent = float(percent)

                response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                p_cur_price = float(response['result'][0]['price'])
                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")
                p_entry = 0
                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                        buy_qty = float(p['size'])
                        p_entry = float(p['entry_price'])
                if have_sell_position:
                    raise Exception("this bot has a short position open.", "warn")
                elif have_buy_position:
                    print("p_cur_price=", p_cur_price)
                    print("p_entry=", p_entry)
                    pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                    print("pnl_percent=", pnl_percent)
                    if pnl_percent >= percent:
                        # Close position
                        response = bybit.create_order(pair, "Market", "Sell", buy_qty, params={
                            'reduce_only': True, 'close_on_trigger': True
                        })
                        if response['info']['order_status'] == "Created":
                            log_success(msg, "Position closed successfully.")
                    else:
                        log_error(msg, "P/L is less than specified percent.", "warn")
                else:
                    raise Exception("there is no active long position to close.", "warn")
            
            elif command == "take-profit-short-2":
                if verbose:
                    print(f"take profit short in {pair}")
                percent = msg.percent
                percent = percent[:-1]
                print(f"specific percent: {percent}")
                percent = float(percent)
                print("percent=", percent)
                response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                p_cur_price = float(response['result'][0]['price'])
                # Check for current positions
                response = bybit.fetch_positions(symbols=[pair])
                have_buy_position = False
                have_sell_position = False
                buy_qty = None
                sell_qty = None
                if len(response) != 2:
                    raise Exception("error getting active positions")
                p_entry = 0.0
                for p in response:
                    if p['side'] == "Sell" and float(p['size']) != 0.0:
                        have_sell_position = True
                        sell_qty = float(p['size'])
                        p_entry = float(p['entry_price'])
                    if p['side'] == "Buy" and float(p['size']) != 0.0:
                        have_buy_position = True
                        buy_qty = float(p['size'])
                if have_buy_position:
                    raise Exception("this bot has a long position open.", "warn")
                elif have_sell_position:
                    pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                    print("pnl_percent=", pnl_percent)
                    if pnl_percent >= percent:
                        # Close short position
                        response = bybit.create_order(pair, "Market", "Buy", sell_qty, params={
                            'reduce_only': True, 'close_on_trigger': True
                        })
                        if response['info']['order_status'] == "Created":
                            log_success(msg, "Position closed successfully.")
                    else:
                        log_error(msg, "P/L is less than specified percent.", "warn")
                else:
                    raise Exception("there is no active short position to close.", "warn")

            elif command == "take-profit-long-3":
                if verbose:
                    print(f"take profit long3 in {pair}")
                percent = msg.percent
                percent = percent[:-1]
                print(f"specific percent: {percent}")
                percent = float(percent)
                has_p, p_type, p_qty, p_sl, p_upnl, p_entry, p_cur_price, side = get_position(bybit, bot_id, pair)
                if has_p:
                    if p_type == 'no':
                        log_error(msg, "there is no active long position to close.", "warn")
                    elif p_type == 'long':
                        pnl_percent = ((p_cur_price - p_entry)/p_entry)*100
                        print(f"PNL percent: {pnl_percent}")
                        print(f"signal percent: {percent}")
                        if pnl_percent >= percent:
                            response = bybit.create_order(pair, "Market", side, p_qty, params={
                                    'reduce_only': True, 'close_on_trigger': True
                                })
                            if response['info']['order_status'] == "Created":
                                log_success(msg, "Position closed successfully.")
                        else:
                            # Get Portfolio Value
                            if verbose:
                                print("Getting portfolio...")
                            response = bybit.fetch_balance()
                            usdt_portfolio = float(response['USDT']['free'])
                            if verbose:
                                print(f"Available USDT Portfolio Value: {usdt_portfolio}")
                            invest_precent = float(config['trade'][f"{pair}_portfolio_percent"])
                            if verbose:
                                print(f"Portfolio percentage: {invest_precent}%")

                            # Get Latest price for symbol
                            response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                            index_price = float(response['result'][0]['price'])
                            if verbose:
                                print(f"Price for {base}: {index_price}")
                            leverage = float(config['trade'][f"{pair}_leverage_multiple"])
                            # Calculate Qty
                            position_qty = round((usdt_portfolio * leverage * (invest_precent / 100)) / index_price, 2)
                            if verbose:
                                print(f"Position qty: {position_qty}")
                            
                            tp_params = {'stopPrice': index_price}
                            # # order = bybit.create_order(symbol, 'TAKE_PROFIT_MARKET', "Sell", position_qty, None, tp_params)
                            # order = bybit.create_order(symbol=symbol, type="TAKE_PROFIT_MARKET", side="Sell", amount=position_qty, price=index_price , params={"base_price": index_price,  "stop_px": index_price, "closePosition": True, "stopPrice": index_price})
                            response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Sell",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False})
                            log_success(msg, "take-profit order created")
                    elif p_type == 'short':
                        log_error(msg, "there is no active long position to close.", "warn")
                else:
                    log_error(msg, "there is no active long position to close.", "warn")
            
            elif command == "take-profit-short-3":
                if verbose:
                    print(f"take profit short3 in {pair}")
                percent = msg.percent
                percent = percent[:-1]
                print(f"specific percent: {percent}")
                percent = float(percent)
                has_p, p_type, p_qty, p_sl, p_upnl, p_entry, p_cur_price, side = get_position(bybit, bot_id, pair)
                if has_p:
                    if p_type == 'no':
                        log_error(msg, "there is no active short position to close.", "warn")
                    elif p_type == 'short':
                        pnl_percent = -((p_cur_price - p_entry)/p_entry)*100
                        print(f"PNL percent: {pnl_percent}")
                        print(f"signal percent: {percent}")
                        if pnl_percent >= percent:
                            response = bybit.create_order(pair, "Market", side, p_qty, params={
                                    'reduce_only': True, 'close_on_trigger': True
                                })
                            if response['info']['order_status'] == "Created":
                                log_success(msg, "Position closed successfully.")
                        else:
                            # Get Portfolio Value
                            if verbose:
                                print("Getting portfolio...")
                            response = bybit.fetch_balance()
                            usdt_portfolio = float(response['USDT']['free'])
                            if verbose:
                                print(f"Available USDT Portfolio Value: {usdt_portfolio}")
                            invest_precent = float(config['trade'][f"{pair}_portfolio_percent"])
                            if verbose:
                                print(f"Portfolio percentage: {invest_precent}%")

                            # Get Latest price for symbol
                            response = bybit.public_linear_get_recent_trading_records({"symbol": symbol, "limit": 1})
                            index_price = float(response['result'][0]['price'])
                            if verbose:
                                print(f"Price for {base}: {index_price}")
                            leverage = float(config['trade'][f"{pair}_leverage_multiple"])
                            # Calculate Qty
                            position_qty = round((usdt_portfolio * leverage * (invest_precent / 100)) / index_price, 2)
                            if verbose:
                                print(f"Position qty: {position_qty}")
                            tp_params = {'stopPrice': index_price}
                            # order = bybit.create_order(symbol, 'TAKE_PROFIT_MARKET', "Buy", position_qty, None, tp_params)
                            # order = bybit.create_order(symbol=symbol, type="TAKE_PROFIT_MARKET", side="Buy", amount=position_qty, price=index_price , params={"base_price":index_price,  "stop_px":index_price, "closePosition": True, "stopPrice": index_price})
                            response = bybit.private_linear_post_order_create({"symbol": symbol,
                                                                           "side": "Buy",
                                                                           "order_type": "Market",
                                                                           "qty": position_qty,
                                                                           "time_in_force": "GoodTillCancel",
                                                                           "close_on_trigger": False,
                                                                           "reduce_only": False})

                            log_success(msg, "take-profit order created")
                    elif p_type == 'long':
                        log_error(msg, "there is no active short position to close.", "warn")
                else:
                    log_error(msg, "there is no active short position to close.", "warn")
            else:
                raise Exception(f"invalid command {command}", "warn")

        except Exception as e:
            severity = "high"
            if len(e.args) >= 2:
                severity = e.args[1]
            log_error(msg, str(e.args[0]), severity)

    release_lock(bot_id)
    print("")

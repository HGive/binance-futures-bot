def clear_pending():
    global pending_buy_order_id, pending_tp_order_id
    pending_buy_order_id = None
    pending_tp_order_id = None

def calc_amount(avbl,percent, leverage, targetBuyPrice, amount_precision):
    avbl_pcnt = avbl*percent # 주문할 양 잔고의 percent%
    avbl_pcnt_xlev = avbl_pcnt*leverage
    amount = avbl_pcnt_xlev / targetBuyPrice
    return round(amount/amount_precision)*amount_precision
    
def calc_price(percent, price, price_precision) :
    return round(price*percent/price_precision)*price_precision


def fetch_order_with_retry(exchange, order_id, symbol, interval):
    try:
        order = exchange.fetch_order(order_id, symbol)
        status = order['status']
        if status in ['open', 'closed']:
            return order
        else:
            logging.warning(f"Unexpected order status: {status}. Retrying...")
            return None
            
    except Exception as e:
        logging.error(f"Error fetching order {order_id}: {e}")
        return None
    
def custom_limit_order(exchange, symbol, side, amount, price ):
    try:
        order = exchange.create_order( symbol=symbol, type="LIMIT", side=side,
            amount=amount, price=price )
        
        # 주문 상태 확인
        status = order['status']
        if status in ['open', 'closed', 'partially_filled']:
            logging.info(f"Limit order created successfully. Status: {status}")
            return order
        else:
            logging.warning(f"Unexpected order status: {status}. Retrying...")
            return None
    except Exception as e:
        logging.error(f"Error creating limit order: {e}")
        return None
    
def custom_tpsl_order(exchange, symbol, type, side, amount, price, stop_price ):
    try:
        order = exchange.create_order(
            symbol=symbol, type=type, side=side, amount=amount,
            price=price, params={'stopPrice': stop_price} )
        
        # 주문 상태 확인
        status = order['status']
        if status in ['open', 'closed', 'partially_filled']:
            logging.info(f"{type} order created successfully. Status: {status}")
            return order
        else:
            logging.warning(f"Unexpected order status: {status}. Retrying...")
            return None
    except Exception as e:
        logging.error(f"Error creating take profit order: {e}")
        return None
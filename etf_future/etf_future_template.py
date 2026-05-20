import datetime as dt
import time
import logging

from optibook.synchronous_client import Exchange

import sys

sys.path.append("/home/workspace/your_optiver_workspace")
from common.libs import calculate_current_time_to_date

exchange = Exchange()
exchange.connect()

logging.getLogger("client").setLevel("ERROR")

ETF_ID = "OB5X_ETF"
FUTURE_ID = "OB5X_202503_F"

while True:
    print(f"")
    print(f"-----------------------------------------------------------------")
    print(f"TRADE LOOP ITERATION ENTERED AT {str(dt.datetime.now()):18s} UTC.")
    print(f"-----------------------------------------------------------------")

    # Implement your algorithm here, make use of the imported function calculate_current_time_to_date to
    # correctly calculate the time to expiry for the future(s).

    print(f"\nSleeping for 5 seconds.")
    time.sleep(5)

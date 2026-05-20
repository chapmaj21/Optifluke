# Optibook API Reference

---

## Overview

# Package `optibook`

## Modules

#### `optibook.common_types`

 — Shared data types and models used across the client and responses.
#### `optibook.exchange_responses`

 — Response objects returned by the exchange.
#### `optibook.synchronous_client`

 — Synchronous client API for interacting with the exchange.
#### `optibook.exporter`

 — Module for exporting data to CSV files with automatic file size management.

---

## Common Types

# Module `optibook.common_types`

## Classes

#### `class Instrument (instrument_id: str, tick_size: float, instrument_type: Optional[InstrumentType] = None, price_change_limit: Optional[PriceChangeLimit] = None, *, expiry: Optional[datetime.datetime] = None, option_kind: Optional[OptionKind] = None, strike: Optional[float] = None, base_instrument_id: Optional[str] = None, interest_rate: Optional[float] = None, index_id: Optional[float] = None, index_constituents: Optional[Dict[str, float]] = None, index_divisor: Optional[float] = None, index_volatility: Optional[float] = None, etf_cash_comp: Optional[float] = None, etf_multiplier: Optional[float] = None, instrument_group: Optional[str] = None)`

 — ### Static methods

#### `def from_dict(instrument_id: str, tick_size: float, price_change_limit: Union[Dict[~KT, ~VT], PriceChangeLimit, None], dict_data: Dict[~KT, ~VT]) -> Instrument`

#### `def from_extra_info_json(instrument_id: str, tick_size: float, price_change_limit: Optional[PriceChangeLimit], json_data: str) -> Instrument`

#### `def to_extra_info_json(instrument) -> str`
#### `class InstrumentType (value, names=None, *, module=None, qualname=None, type=None, start=1)`

 — An enumeration.

### Ancestors

-   enum.Enum

### Class variables

#### `var INDEX_FUTURE`

#### `var INDEX_OPTION`

#### `var INDEX_TRACKING_ETF`

#### `var STOCK`

#### `var STOCK_FUTURE`

#### `var STOCK_OPTION`
#### `class OptionKind (value, names=None, *, module=None, qualname=None, type=None, start=1)`

 — An enumeration.

### Ancestors

-   enum.Enum

### Class variables

#### `var CALL`

#### `var PUT`
#### `class OrderStatus`

 — Summary of an order.

## Attributes

- **`order_id`** (`int`) — The id of the order.
- **`instrument_id`** (`str`) — The id of the traded instrument.
- **`price`** (`float`) — The price at which the instrument traded.
- **`volume`** (`int`) — The volume that was traded.
- **`side`** (`'bid' or 'ask'`) — If 'bid' this is a bid order. If 'ask' this is an ask order.
#### `class PriceBook (*, timestamp=None, instrument_id=None, bids=None, asks=None)`

 — An order book at a specific point in time.

## Attributes

- **`timestamp`** (`datetime.datetime`) — The time of the snapshot.
- **`instrument_id`** (`str`) — The id of the instrument the book is on.
- **`bids`** (`List[PriceVolume]`) — List of price points and volumes representing all bid orders. Sorted from highest price to lowest price (i.e. from best to worst).
- **`asks`** (`List[PriceVolume]`) — List of price points and volumes representing all ask orders. Sorted from lowest price to highest price (i.e. from best to worst).
#### `class PriceChangeLimit (absolute_change: float, relative_change: float)`

#### `class PriceVolume (price, volume)`

 — Bundles a price and a volume

## Attributes

- **`price`** (`float`)

- **`volume`** (`int`)

### Instance variables

#### `prop price_width`

#### `prop volume_width`
#### `class SingleSidedBooking`

#### `class SocialMediaFeed (*, timestamp=None, post=None, meta_data=None)`

#### `class Trade`

 — A private trade.

A private trade is a trade in which you were involved, i.e. a trade in which you were either a buyer or a seller.

## Attributes

- **`timestamp`** (`datetime.datetime`) — The time of the trade.
- **`order_id`** (`int`) — The id of the order that traded.
- **`trade_id`** (`int`) — Id of the trade
- **`instrument_id`** (`str`) — The id of the traded instrument.
- **`price`** (`float`) — The price at which the instrument traded.
- **`volume`** (`int`) — The volume that was traded.
- **`side`** (`'bid' or 'ask'`) — If 'bid' you bought. If 'ask' you sold.
#### `class TradeTick (*, timestamp=None, instrument_id=None, price=None, volume=None, aggressor_side=None, buyer=None, seller=None, trade_id=None)`

 — A public trade.

A public trade is a trade between any two parties, i.e. a trade in which you might not have been involved.

## Attributes

- **`timestamp`** (`datetime.datetime`) — The time of the trade.
- **`instrument_id`** (`str`) — The id of the traded instrument.
- **`price`** (`float`) — The price at which the instrument traded.
- **`volume`** (`int`) — The volume that was traded.
- **`aggressor_side`** (`'bid' or 'ask'`) — The side of the aggressive party. If 'bid' then the initiator (aggressor) of the trade bought. If 'ask' then the initiator (aggressor) of the trade sold.
- **`buyer`** (`str`) — Name of buyer.
- **`seller`** (`str`) — Name of seller.
- **`trade_id`** (`int`) — Id of the trade

---

## Synchronous Client

# Module `optibook.synchronous_client`

## Classes

#### `class Exchange (host: str = None, info_port: int = None, exec_port: int = None, username: str = None, password: str = None, admin_password: str = None, full_message_logging: bool = False, max_nr_trade_history: int = 100)`

 — Initiate an exchange client instance. This is the class you should use to interact with the exchange, i.e. send orders or delete orders, get the newest trades, etc.

## Parameters

- **`host`** (`str`) — The network location the Exchange Server runs on.
- **`info_port`** (`int`) — The port of the Info interface exposed by the Exchange.
- **`exec_port`** (`int`) — The port of the Execution interface exposed by the Exchange.
- **`username`** (`str`) — Your username.
- **`password`** (`str`) — Your password.
- **`admin_password`** (`str`) — Reserved for dedicated clients only and can be left empty.
- **`full_message_logging`** (`bool`) — If set to to True enables logging on VERBOSE level, displaying among others all messages sent to and received from the exchange.
- **`max_nr_trade_history`** (`int`) — Keep at most this number of trades per instrument in history. Older trades will be removed automatically

### Methods

#### `def amend_order(self, instrument_id: str, *, order_id: int, volume: int) -> AmendOrderResponse`

 — Amend a specific outstanding limit order on an instrument. E.g. to change its volume.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to delete a limit order for.
- **`order_id`** (`int`) — The order\_id of the limit order to delete.
- **`volume`** (`str`) — The new volume to change the order to.

## Returns

`AmendOrderResponse`

 — The AmendOrderResponse returned by the server, containing the success flag as well as error reason should the request have failed. See the doc of that type for more info.
#### `def connect(self) -> None`

 — Attempt to connect to the exchange. Only a single connection can be made on a single username.
#### `def delete_order(self, instrument_id: str, *, order_id: int) -> DeleteOrderResponse`

 — Delete a specific outstanding limit order on an instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to delete a limit order for.
- **`order_id`** (`int`) — The order\_id of the limit order to delete.

## Returns

`DeleteOrderResponse`

 — The DeleteOrderResponse returned by the server, containing the success flag as well as error reason should the request have failed. See the doc of that type for more info.
#### `def delete_orders(self, instrument_id: str) -> None`

 — Delete all outstanding orders on an instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to delete the orders for.
#### `def disconnect(self) -> None`

 — Disconnect from the exchange.
#### `def get_cash(self) -> float`

 — Get your total cash position.

## Returns

`float`

 — Returns total cash position of the client arising from all cash exchanged on previous buy and sell trades in all instruments.
#### `def get_instruments(self) -> Dict[str, Instrument]`

 — Returns all existing instruments on the exchange

## Returns

`typing.Dict[str, Instrument]`

 — Dict of instrument\_id to the instrument definition.
#### `def get_last_price_book(self, instrument_id: str) -> PriceBook`

 — Returns the last received limit order book state for an instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to obtain the limit order book for.

## Returns

`PriceBook`

 — Returns the last received limit order book state for an instrument.
#### `def get_outstanding_orders(self, instrument_id: str) -> Dict[int, OrderStatus]`

 — Returns the client's currently outstanding limit orders on an instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to obtain outstanding orders for.

## Returns

`typing.Dict[int, OrderStatus]`

 — Dictionary mapping order\_id to OrderStatus objects representing the client's currently outstanding limit orders on an instrument.
#### `def get_pnl(self, valuations: Dict[str, float] = None) -> float`

 — Calculates PnL based on current instrument and cash positions.

For any non-zero position: If the valuations dictionary is provided, uses the valuation provided. If no instrument valuation is provided, falls back on the price of the last public tradetick. If valuation is not provided and no public tradetick is available, no PnL can be calculated.

## Parameters

- **`valuations`** (`typing.Dict[str, float]`) — Optional, dictionary mapping instrument\_id to current instrument valuation.

## Returns

`float`

 — Your current PnL, valued at the last-traded price if no valuations are provided.
#### `def get_positions(self) -> Dict[str, int]`

 — Get your current positions.

## Returns

`typing.Dict[str, int]`

 — Returns a dictionary mapping instrument\_id to the current position in the instrument, expressed in amount of lots held.
#### `def get_positions_and_cash(self) -> Dict[str, Dict[~KT, ~VT]]`

 — Get your current positions and cash.

## Returns

`typing.Dict[str, typing.Dict]`

 — Returns a dictionary mapping instrument\_id to dictionary of 'volume' and 'cash'. The volume is the current amount of lots held in the instrument and the cash is the current cash position arising from previous buy and sell trades in the instrument.
#### `def get_social_media_feeds_history(self) -> List[SocialMediaFeed]`

 — Returns the new social media feeds since connection (up to a max cap)

## Returns

`typing.List[SocialMediaFeed]`

 — Returns the new social media feeds since connection (up to a max cap)
#### `def get_tradable_instruments(self) -> Dict[str, Instrument]`

 — Returns all tradable instruments on the exchange. This excludes instruments which are expired or for which trading is paused.

## Returns

`typing.Dict[str, Instrument]`

 — Dict of instrument\_id to the instrument definition.
#### `def get_trade_history(self, instrument_id: str) -> List[Trade]`

 — Returns all private trades received for an instrument since the start of this Exchange Client (but capped by max\_nr\_total\_trades). If the total number of trades per instrument is larger than max\_nr\_total\_trades, older trades will not be returned by this function.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to obtain the private trade history for.

## Returns

`typing.List[Trade]`

 — Returns all private trades received for an instrument since the start of this Exchange Client (but capped by max\_nr\_total\_trades).
#### `def get_trade_tick_history(self, instrument_id: str) -> List[TradeTick]`

 — Returns all public trade ticks received for an instrument since the start of this Exchange Client (but capped by max\_nr\_total\_trades). If the total number of trades per instrument is larger than max\_nr\_total\_trades, older trades will not be returned by this function.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to obtain the trade tick history for.

## Returns

`typing.List[TradeTick]`

 — Returns all public trade ticks received for an instrument since the start of this Exchange Client (but capped by max\_nr\_total\_trades).
#### `def insert_order(self, instrument_id: str, *, price: float, volume: int, side: str, order_type: str = 'limit') -> InsertOrderResponse`

 — Insert a limit or IOC order on an instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to insert the order on.
- **`price`** (`float`) — The (limit) price of the order.
- **`volume`** (`int`) — The number of lots in the order.
- **`side`** (`str`) — 'bid' or 'ask', a bid order is an order to buy while an ask order is an order to sell.
- **`order_type`** (`str`) — 'limit' or 'ioc', limit orders stay in the book while any remaining volume of an IOC that is not immediately matched is cancelled.

## Returns

`InsertOrderResponse`

 — The InsertOrderResponse returned by the server, containing the success flag as well as an order id and an error reason should the request have failed. See the doc of that type for more info.
#### `def is_connected(self) -> bool`

 — Tells you if the client is currently connected to the exchange.

## Returns

`bool`

 — True if you are connected, otherwise false.
#### `def poll_new_social_media_feeds(self) -> List[SocialMediaFeed]`

 — Returns the new social media feeds, posted since the last time this function was called. For admin clients, the feed contains the post and metadata

## Returns

`typing.List[SocialMediaFeed]`

 — Returns the new social media feeds since connection (up to a max cap)
#### `def poll_new_trade_ticks(self, instrument_id: str) -> List[TradeTick]`

 — Returns the public trades received for an instrument since the last time this function was called for that instrument. Public trades are trades between two other parties, in which you may or may not have been involved.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to poll the trade ticks for.

## Returns

`typing.List[TradeTick]`

 — Returns the public trades received for an instrument since the last time this function was called for that instrument.
#### `def poll_new_trades(self, instrument_id: str) -> List[Trade]`

 — Returns the private trades received for an instrument since the last time this function was called for that instrument.

## Parameters

- **`instrument_id`** (`str`) — The instrument\_id of the instrument to poll the private trades for.

## Returns

`typing.List[Trade]`

 — Returns the private trades received for an instrument since the last time this function was called for that instrument.

---

## Exchange Responses

# Module `optibook.exchange_responses`

## Classes

#### `class AmendOrderResponse (success: bool, error_reason: Optional[str])`

 — The exchange response upon amending an order.

## Attributes

- **`success`** (`bool`) — A success flag indicating whether the request was successful or not. If it was not, error\_reason is set.
- **`error_reason`** (`Optional[str]`) — An error reason in case the insert was not successful.
#### `class DeleteOrderResponse (success: bool, error_reason: Optional[str])`

 — The server response upon deleting an order.

## Attributes

- **`success`** (`bool`) — A success flag indicating whether the request was successful or not. If it was not, error\_reason is set.
- **`error_reason`** (`Optional[str]`) — An error reason in case the insert was not successful.
#### `class InsertOrderResponse (success: bool, order_id: Optional[int], error_reason: Optional[str])`

 — The exchange response upon inserting an order.

## Attributes

- **`success`** (`bool`) — A success flag indicating whether the request was successful or not. If it was, order\_id is set, otherwise error\_reason is set.
- **`order_id`** (`Optional[int]`) — The id of the order which was inserted. The order\_id can be used to delete or amend a limit order later. If None, the order insertion failed, and an error\_reason will be set.
- **`error_reason`** (`Optional[str]`) — An error reason in case the insert was not successful.

---

## Exporter

# Module `optibook.exporter`

The Exporter module provides functionality for exporting trading data to CSV files with automatic file size management and folder organization.

## Overview

The `Exporter` class manages the export of trading data to CSV files, automatically handling file size limits and providing debugging information about storage usage.

## Key Features

-   Automatic folder creation for exports
-   File size limit management (2GB default)
-   CSV format with timestamp columns
-   Bulk export capabilities
-   Storage usage monitoring
-   Reset functionality to clear all exports

## Usage Example

```
from optibook.exporter import Exporter

# Initialize exporter
exporter = Exporter(debugging=True)

# Prepare data for export
data = {
    "trades.csv": [
        ["instrument", "price", "volume", "side"],
        ["PHILIPS_A", "36.50", "100", "BID"],
        ["PHILIPS_B", "36.55", "50", "ASK"]
    ],
    "orders.csv": [
        ["order_id", "instrument", "price", "volume"],
        ["123", "PHILIPS_A", "36.45", "200"],
        ["124", "PHILIPS_B", "36.60", "150"]
    ]
}

# Export data
exporter.export(data)

# Reset all exports if needed
exporter.reset()
```

## Classes

#### `class Exporter (debugging: bool = False)`

 — A class for exporting trading data to CSV files with automatic file management.

The Exporter class provides functionality to export structured data to CSV files, manage file size limits, and monitor storage usage. It automatically creates an 'exports' folder and handles file operations safely.

### Attributes

- **`debugging`** (`bool`) — If True, prints storage usage information after each export.

### Parameters

- **`debugging`** (`bool, optional`) — Enable debugging mode to display storage usage information. Default is False.

### Methods

#### `def __init__(self, debugging: bool = False) -> None`

 — Initialize the Exporter with optional debugging mode.

Creates an 'exports' folder if it doesn't exist and sets up the file size limit (2048 MB).

### Parameters

- **`debugging`** (`bool, optional`) — If True, prints storage usage information after each export. Default is False.
#### `def export(self, data: Dict[str, List[List[str]]]) -> None`

 — Export data to CSV files with automatic timestamp addition.

Exports data to the specified files. The data is structured as Dict\[str, List\[List\[str\]\]\] where the key describes the filename. The value is a list of lists which define the rows and columns to write to the file. Each row will have a UTC timestamp automatically prepended.

### Parameters

- **`data`** (`Dict[str, List[List[str]]]`) — A dictionary where keys are filenames (e.g., "trades.csv") and values are lists of rows, where each row is a list of column values.

### Returns

`None`

 — The method writes to files but doesn't return a value.

### Notes

-   If the folder size exceeds the limit (2048 MB), the export will be skipped and a warning message will be printed.
-   Each row in the exported file will have a UTC ISO timestamp as the first column.
-   Files are opened in append mode, so data is added to existing files.

### Example

```
data = {
    "trades.csv": [
        ["instrument", "price", "volume"],
        ["PHILIPS_A", "36.50", "100"],
        ["PHILIPS_B", "36.55", "50"]
    ]
}
exporter.export(data)
# Output file will have format: [timestamp, instrument, price, volume]
```
#### `def reset(self) -> None`

 — Remove all files in the exports folder.

This method deletes all files in the exports directory, effectively resetting the export storage. Each removed file will be printed to the console.

### Returns

`None`

 — The method removes files but doesn't return a value.

### Warning

This operation is irreversible. All exported data will be permanently deleted.

### Example

```
exporter = Exporter()
exporter.reset()
# Output: Removed: exports/trades.csv
#         Removed: exports/orders.csv
```

### Private Methods

#### `def __write(self, timestamp: str, filename: str, data: List[List[str]]) -> None`

 — Write data rows to a CSV file with timestamp prepended to each row.

This private method handles the actual file writing operation, adding the provided timestamp as the first column of each row.
#### `def __get_folder_size(self) -> float`

 — Calculate the total size of all files in the exports folder.

### Returns

`float`

 — The total size of all files in megabytes (MB).

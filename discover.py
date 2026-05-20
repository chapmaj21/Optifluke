"""Run this on the devenv to print all available instruments and their properties."""
import time
from optibook.synchronous_client import Exchange

exchange = Exchange()
exchange.connect()

instruments = exchange.get_instruments()

print(f"\n{'='*80}")
print(f"AVAILABLE INSTRUMENTS ({len(instruments)} total)")
print(f"{'='*80}\n")

for iid, inst in sorted(instruments.items()):
    parts = [f"type={inst.instrument_type}"]
    parts.append(f"tick={inst.tick_size}")
    if inst.expiry:
        parts.append(f"expiry={inst.expiry}")
    if inst.strike:
        parts.append(f"strike={inst.strike}")
    if inst.option_kind:
        parts.append(f"kind={inst.option_kind}")
    if inst.base_instrument_id:
        parts.append(f"base={inst.base_instrument_id}")
    print(f"  {iid:40s}  {', '.join(parts)}")

print(f"\n{'='*80}")
print("DUAL LISTINGS (instruments ending in _DUAL):")
duals = [iid for iid in instruments if "_DUAL" in iid]
if duals:
    for d in sorted(duals):
        base = d.replace("_DUAL", "")
        exists = base in instruments
        print(f"  {d}  ->  base {base} {'EXISTS' if exists else 'MISSING'}")
else:
    print("  None found")

print(f"\nFUTURES (instruments ending in _F):")
futs = [iid for iid in instruments if iid.endswith("_F")]
for f in sorted(futs):
    print(f"  {f}  expiry={instruments[f].expiry}")

print(f"\nOPTIONS (STOCK_OPTION type):")
from optibook.common_types import InstrumentType
opts = [iid for iid, inst in instruments.items() if inst.instrument_type == InstrumentType.STOCK_OPTION]
for o in sorted(opts):
    inst = instruments[o]
    print(f"  {o}  strike={inst.strike} kind={inst.option_kind} base={inst.base_instrument_id} expiry={inst.expiry}")

print(f"\nSTOCKS:")
stocks = [iid for iid, inst in instruments.items()
          if inst.instrument_type == InstrumentType.STOCK and "_DUAL" not in iid]
for s in sorted(stocks):
    print(f"  {s}")

print(f"\nETFs:")
etfs = [iid for iid in instruments if "ETF" in iid]
for e in sorted(etfs):
    print(f"  {e}")

print(f"\n{'='*80}")
print("Copy-paste this output and send it back so I can fix the algos.")
print(f"{'='*80}")

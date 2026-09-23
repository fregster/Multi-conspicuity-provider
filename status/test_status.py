"""Run: python3 test_status.py"""

import os

os.environ["ULTRAFEEDER_CONFIG"] = "adsb,in.adsb.lol,30004,beast_reduce_plus_out,uuid=x;mlat,in.adsb.lol,31090,uuid=x"
import status

assert status.MLAT == [("in.adsb.lol", "31090")]
prom = 'readsb_net_connector_status{host="in.adsb.lol",port="30004"} 3204\nreadsb_uptime 5\n'
assert status.beast(prom) == [{"host": "in.adsb.lol", "up_s": 3204}]
print("ok")

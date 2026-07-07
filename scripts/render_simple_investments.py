#!/usr/bin/env python3

# Public weekly pages now update visible current prices client-side from
# /data/investments/room_quotes.json, the same shared quote file used by the
# Investing room. Do not overwrite those pages with stale rendered prices.

if __name__ == "__main__":
    print("Skipped static weekly render: public pages use shared live room_quotes.json")

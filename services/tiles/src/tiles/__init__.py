"""twiner tile-join function: base PMTiles tile + a scenario's thin
results -> a joined MVT tile.

`handler.py` (Lambda) is the only place here that imports AWS/Lambda
concerns; `tile_join.py` is pure and I/O-free, shared with
services/scenario's local dev server (see that package's own tile_join.py).
"""

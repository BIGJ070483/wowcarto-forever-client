"""Decode native region outlines and project calibrated reference maps onto Forever terrain."""
from __future__ import annotations

import struct

# Measured on the prepared 1.20 maps and Forever 1.60.1.70755 terrain.
# Each matrix maps quarter-scale reference image pixels to 1/16-scale terrain pixels.
# Evidence and inlier coordinates: outputs/zone-registration-20261104/*-matches.json.
FOREVER_REGISTRATION = {
    1000: ('Kalimdor', (-1023.984375, -1279.98046875),
           ((0.999670918, -0.000176892912, 288.169659),
            (0.000176892912, 0.999670918, 160.206285))),
    0: ('Azeroth', (-1279.98046875, -1791.97265625),
        ((0.999749061, -0.0000500680764, 224.116846),
         (0.0000500680764, 0.999749061, 176.166913))),
}


def decode_region_outline(record):
    """Decode the observed S_ initial big-endian point and signed deltas.

    The native vertical axis is inverted relative to the map display. Reject
    malformed data or a bounding box that excludes its own region marker.
    """
    encoded = record.get('links', '')
    if not encoded.startswith('S_'):
        return None
    raw = encoded[2:]
    if len(raw) < 24 or (len(raw) - 16) % 8 or len(raw) > 200_000:
        return None
    try:
        data = bytes.fromhex(raw)
        x, y = struct.unpack_from('>ii', data)
        points = [(x / 256, y / 256)]
        for pos in range(8, len(data), 4):
            dx, dy = struct.unpack_from('>hh', data, pos)
            x += dx
            y -= dy
            points.append((x / 256, y / 256))
    except (ValueError, struct.error):
        return None
    bounds = polygon_bounds(points)
    x0, y0, x1, y1 = bounds
    if not (x0 <= record['wx'] <= x1 and y0 <= record['wy'] <= y1):
        return None
    return points


def polygon_bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def point_in_polygon(x, y, points):
    """Include boundary points, then use an even-odd crossing test."""
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous; x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-8 and min(x1,x2) <= x <= max(x1,x2) and min(y1,y2) <= y <= max(y1,y2):
            return True
        if (y1 > y) != (y2 > y) and x < (x2-x1) * (y-y1) / (y2-y1) + x1:
            inside = not inside
        previous = current
    return inside


def project_forever(group_id, point):
    name, (x0, y0), matrix = FOREVER_REGISTRATION[group_id]
    x, y = (point[0] - x0) * .25, (point[1] - y0) * .25
    return (16 * (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]),
            16 * (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]))


def project_historical_records(records):
    """Project historical records without dropping points for missing terrain."""
    for row in records:
        registration = FOREVER_REGISTRATION.get(row['group_id'])
        if not registration or row['category'] == 18:
            continue
        wx, wy = project_forever(row['group_id'], (row['wx'], row['wy']))
        yield dict(row, wx=wx, wy=wy, group_id=registration[0],
                   reference_region=row['region'], region=registration[0],
                   historical_unverified=True)

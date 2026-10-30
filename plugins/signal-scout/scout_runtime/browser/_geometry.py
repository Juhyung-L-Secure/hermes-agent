from ._errors import ActionRejected


def _clip(points, width, height):
    for axis, bound, greater in ((0, 0, True), (0, width, False),
                                 (1, 0, True), (1, height, False)):
        result = []
        if not points:
            break
        previous = points[-1]
        for point in points:
            inside = (point[axis] >= bound) if greater else (point[axis] <= bound)
            was_inside = (previous[axis] >= bound) if greater else (previous[axis] <= bound)
            if inside != was_inside:
                ratio = (bound - previous[axis]) / (point[axis] - previous[axis])
                result.append(tuple(previous[i] + ratio * (point[i] - previous[i]) for i in (0, 1)))
            if inside:
                result.append(point)
            previous = point
        points = result
    return points


def _project(point, quad, width, height, *, inverse=False):
    a, b, c, d = [quad[i:i + 2] for i in range(0, 8, 2)]
    dx, dy = a[0] - b[0] + c[0] - d[0], a[1] - b[1] + c[1] - d[1]
    if dx == dy == 0:
        g = h = 0
    else:
        x1, x2, y1, y2 = b[0] - c[0], d[0] - c[0], b[1] - c[1], d[1] - c[1]
        divisor = x1 * y2 - x2 * y1
        if not divisor:
            raise ActionRejected("Target has no visible interaction point.")
        g, h = (dx * y2 - x2 * dy) / divisor, (x1 * dy - dx * y1) / divisor
    ax, bx = b[0] - a[0] + g * b[0], d[0] - a[0] + h * d[0]
    ay, by = b[1] - a[1] + g * b[1], d[1] - a[1] + h * d[1]
    if inverse:
        x, y = point
        aa, bb, cc, dd = ax - x * g, bx - x * h, ay - y * g, by - y * h
        determinant = aa * dd - bb * cc
        if not determinant:
            raise ActionRejected("Target has no visible interaction point.")
        return (((x - a[0]) * dd - bb * (y - a[1])) / determinant * width,
                (aa * (y - a[1]) - (x - a[0]) * cc) / determinant * height)
    u, v = point[0] / width, point[1] / height
    denominator = g * u + h * v + 1
    if not denominator:
        raise ActionRejected("Target has no visible interaction point.")
    return ((ax * u + bx * v + a[0]) / denominator,
            (ay * u + by * v + a[1]) / denominator)

import numpy as np


def pack():
    n = 26
    centers = [(0.5, 0.5)]
    for i in range(8):
        a = 2 * np.pi * i / 8
        centers.append((0.5 + 0.3 * np.cos(a), 0.5 + 0.3 * np.sin(a)))
    for i in range(17):
        a = 2 * np.pi * i / 17
        centers.append((0.5 + 0.45 * np.cos(a), 0.5 + 0.45 * np.sin(a)))
    return np.array(centers), [0.024] * n

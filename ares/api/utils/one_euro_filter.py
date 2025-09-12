import numpy as np

class OneEuroFilter:
    def __init__(self, min_cutoff=1.0, beta=0.01):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.dx_prev = 0.0
        self.x_prev = None
        self.timestamp_prev = None

    def filter(self, x, timestamp):
        if self.x_prev is None:
            self.x_prev = x
            self.timestamp_prev = timestamp
            return x

        dt = timestamp - self.timestamp_prev
        if dt <= 0:
            return x

        dx = (x - self.x_prev) / dt
        cutoff = self.min_cutoff + self.beta * abs(dx)
        alpha = 1.0 / (1.0 + (1.0 / (2.0 * np.pi * cutoff * dt)))
        x_filtered = alpha * x + (1.0 - alpha) * self.x_prev

        self.x_prev = x_filtered
        self.dx_prev = dx
        self.timestamp_prev = timestamp

        return x_filtered
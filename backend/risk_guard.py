class AuroraRiskGuard:
    def __init__(self):
        self.active_signals = set()

    def validate_signal(self, signal):
        if self.is_duplicate_signal(signal):
            return False
        if self.is_conflicting_signal(signal):
            return False
        if not self.has_valid_data(signal):
            return False
        return True

    def is_duplicate_signal(self, signal):
        return signal['id'] in self.active_signals

    def is_conflicting_signal(self, signal):
        # Logic to check for conflicting signals on the same asset
        return False

    def has_valid_data(self, signal):
        required_fields = ['ativo', 'direcao', 'expiracao', 'payout']
        return all(field in signal for field in required_fields)

    def register_signal(self, signal):
        if self.validate_signal(signal):
            self.active_signals.add(signal['id'])
            return True
        return False

    def clear_expired_signals(self):
        # Logic to clear signals that have expired
        pass

    def reset(self):
        self.active_signals.clear()
from typing import List, Dict, Any
import requests

class BrokerDataAdapter:
    def __init__(self, api_url: str):
        self.api_url = api_url

    def fetch_candle_data(self, asset: str, timeframe: str, limit: int = 100) -> List[Dict[str, Any]]:
        response = requests.get(f"{self.api_url}/candles", params={"asset": asset, "timeframe": timeframe, "limit": limit})
        response.raise_for_status()
        return response.json()

    def fetch_current_price(self, asset: str) -> float:
        response = requests.get(f"{self.api_url}/price", params={"asset": asset})
        response.raise_for_status()
        return response.json().get("price")

    def validate_data(self, data: Dict[str, Any]) -> bool:
        required_keys = ["timestamp", "open", "high", "low", "close", "volume"]
        return all(key in data for key in required_keys)

    def get_candle_data(self, asset: str, timeframe: str) -> List[Dict[str, Any]]:
        candles = self.fetch_candle_data(asset, timeframe)
        valid_candles = [candle for candle in candles if self.validate_data(candle)]
        return valid_candles

    def get_price_data(self, asset: str) -> float:
        return self.fetch_current_price(asset)
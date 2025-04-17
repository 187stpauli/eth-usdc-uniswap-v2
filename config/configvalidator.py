from decimal import Decimal, InvalidOperation
from eth_utils import decode_hex
from dotenv import load_dotenv
from eth_keys import keys
import requests
import logging
import json
import os
import re

MIN_AMOUNT = Decimal(0.0001)
logger = logging.getLogger(__name__)
load_dotenv(dotenv_path=".env")


class ConfigValidator:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config_data = self.load_config()

    def load_config(self) -> dict:
        """Загружает конфигурационный файл"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            logging.error(f"Файл конфигурации {self.config_path} не найден.")
            exit(1)
        except json.JSONDecodeError:
            logging.error(f"Ошибка разбора JSON в файле конфигурации {self.config_path}.")
            exit(1)

    @staticmethod
    async def resolve_private_key(key: str) -> str:

        if key.startswith("ENV:"):
            key_name = key[4:]
            raw = os.getenv("PRIVATE_KEYS")
            if not raw:
                logging.error("Ошибка: переменная окружения 'PRIVATE_KEYS' не найдена.")
                exit(1)

            try:
                key_map = json.loads(raw)
            except json.JSONDecodeError:
                logging.error("Ошибка: 'PRIVATE_KEYS' в .env имеет некорректный JSON формат.")
                exit(1)

            if key_name not in key_map:
                logging.error(f"Ошибка: ключ '{key_name}' не найден в переменной PRIVATE_KEYS.")
                exit(1)

            return key_map[key_name]

        return key

    async def validate_config(self) -> dict:
        """Валидация всех полей конфигурации"""

        await self.validate_required_keys()

        if "from_token" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'from_token' в конфигурации.")
            exit(1)

        if "to_token" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'to_token' в конфигурации.")
            exit(1)

        if "proxy" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'proxy' в конфигурации.")
            exit(1)

        if "amount" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'amount' в конфигурации.")
            exit(1)

        if self.config_data["from_token"] == self.config_data["to_token"]:
            logging.error("from_token и to_token должны отличаться.")
            exit(1)

        if "private_key" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'private_key' в конфигурации.")
            exit(1)

        if "network" not in self.config_data:
            logging.error("Ошибка: Отсутствует 'network' в конфигурации.")
            exit(1)

        load_dotenv(dotenv_path="../.env")

        resolved_key = await self.resolve_private_key(self.config_data["private_key"])

        await self.validate_private_key(resolved_key)

        self.config_data["private_key"] = resolved_key

        await self.validate_from_token(self.config_data["from_token"])
        await self.validate_to_token(self.config_data["to_token"])
        await self.validate_network(self.config_data["network"])
        await self.validate_amount(self.config_data["amount"])
        await self.validate_proxy(self.config_data["proxy"])

        return self.config_data

    async def validate_required_keys(self):
        required_keys = [
            "from_token",
            "to_token",
            "amount",
            "private_key",
            "proxy",
            "network"
        ]

        for key in required_keys:
            if key not in self.config_data:
                logging.error(f"Ошибка: отсутствует обязательный ключ '{key}' в settings.json")
                exit(1)

    @staticmethod
    async def validate_private_key(private_key: str) -> None:
        """Валидация приватного ключа"""
        try:
            private_key_bytes = decode_hex(private_key)
            _ = keys.PrivateKey(private_key_bytes)
        except (ValueError, Exception):
            logging.error("Ошибка: Некорректный 'private_key' в конфигурации.")
            exit(1)

    @staticmethod
    async def validate_network(network: str) -> None:
        """Валидация названия сети"""
        networks = [
            "OPTIMISM", "BSC", "POLYGON", "ARBITRUM"
        ]
        if network not in networks:
            logging.error("Ошибка: Неподдерживаемая сеть! Введите одну из поддерживаемых сетей.")
            exit(1)

    @staticmethod
    async def validate_from_token(from_token: str) -> None:
        """Валидация названия исходного токена"""
        from_tokens = [
            "ETH"
        ]
        if from_token not in from_tokens:
            logging.error("Ошибка: Неподдерживаемый токен! Введите ETH.")
            exit(1)

    @staticmethod
    async def validate_to_token(to_token: str) -> None:
        """Валидация названия конечного токена"""
        to_tokens = [
            "USDC"
        ]
        if to_token not in to_tokens:
            logging.error("Ошибка: Неподдерживаемый токен! Введите USDC.")
            exit(1)

    @staticmethod
    async def validate_proxy(proxy: str) -> None:
        """Валидация прокси-адреса"""
        if not proxy:
            logging.info("Прокси не указан — пропуск валидации.\n")
            return

        pattern = r"^(?P<login>[^:@]+):(?P<password>[^:@]+)@(?P<host>[\w.-]+):(?P<port>\d+)$"
        match = re.match(pattern, proxy)
        if not match:
            logging.error("Ошибка: Неверный формат прокси! Должен быть 'login:pass@host:port'.")
            exit(1)

        proxy_url = {
            "http": f"http://{proxy}"
        }
        response = requests.get("https://httpbin.org/ip", proxies=proxy_url, timeout=5)
        if response.status_code != 200:
            logging.error("Ошибка: 'proxy' нерабочий или вернул неверный статус-код!")
            exit(1)

    @staticmethod
    async def validate_amount(amount_raw: float) -> None:
        """Валидация количества токенов"""
        if not isinstance(amount_raw, (str, int, float)):
            raise ValueError(f"Количество должно быть строкой или числом, но имеет тип {type(amount_raw)}.")

        try:
            amount = Decimal(str(amount_raw))
        except InvalidOperation:
            logging.error("Ошибка количества токенов! Введено невалидное значение.")
            exit(1)

        if amount <= 0:
            logging.error("Количество токенов должно быть больше нуля.")
            exit(1)

        if amount < MIN_AMOUNT:
            logging.error("Количество токенов для отправки слишком мало, введите значение больше 0.0001.")
            exit(1)

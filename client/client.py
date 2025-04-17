from functools import wraps
from aiohttp import ClientHttpProxyError
from web3.middleware.geth_poa import async_geth_poa_middleware
from web3.exceptions import TransactionNotFound
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.contract import AsyncContract
from typing import Optional, Union
from web3.types import TxParams
from hexbytes import HexBytes
from client.networks import Network
import asyncio
import logging
import json

with open("abi/erc20_abi.json", "r", encoding="utf-8") as file:
    ERC20_ABI = json.load(file)

with open("abi/uniswap_router_v2.json", "r", encoding="utf-8") as f:
    UNISWAP_ROUTER_ABI = json.load(f)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)


def retry_on_proxy_error(max_attempts: int = 3, fallback_no_proxy: bool = True):
    """Декоратор для повторных попыток при ошибках прокси."""

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            attempts = 0
            last_error = None
            while attempts < max_attempts:
                try:
                    return await func(self, *args, **kwargs)
                except ClientHttpProxyError as e:
                    attempts += 1
                    last_error = e
                    logger.warning(f"Ошибка прокси (попытка {attempts}/{max_attempts}): {e}")
                    if attempts == max_attempts and fallback_no_proxy:
                        logger.info("Отключаем прокси для последней попытки")
                        self._disable_proxy()
                        try:
                            return await func(self, *args, **kwargs)
                        except ClientHttpProxyError as e:
                            last_error = e
                    await asyncio.sleep(1)
            raise ValueError(f"Не удалось выполнить запрос после {max_attempts} попыток: {last_error}")

        return wrapper

    return decorator


class Client:
    def __init__(self, from_address: str, to_address: str, chain_id: int, rpc_url: str, private_key: str,
                 amount: float, router_address: str, explorer_url: str, proxy: Optional[str] = None):
        request_kwargs = {"proxy": f"http://{proxy}"} if proxy else {}
        self.uniswap_router_abi = UNISWAP_ROUTER_ABI
        self.router_address = router_address
        self.from_address = from_address
        self.explorer_url = explorer_url
        self.private_key = private_key
        self.to_address = to_address
        self.chain_id = chain_id
        self.amount = amount
        self.rpc_url = rpc_url
        self.proxy = proxy

        # Определяем сеть
        if isinstance(chain_id, str):
            self.network = Network.from_name(chain_id)
        else:
            self.network = Network.from_chain_id(chain_id)

        self.chain_id = self.network.chain_id

        # Инициализация AsyncWeb3
        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url, request_kwargs=request_kwargs))
        # Применяем middleware для PoA-сетей
        if self.network.is_poa:
            self.w3.middleware_onion.clear()
            self.w3.middleware_onion.inject(async_geth_poa_middleware, layer=0)

        self.eip_1559 = True
        self.address = self.w3.to_checksum_address(
            self.w3.eth.account.from_key(self.private_key).address)

    # Получение баланса нативного токена
    async def get_native_balance(self) -> float:
        """Получает баланс нативного токена в ETH/BNB/MATIC и т.д."""
        balance_wei = await self.w3.eth.get_balance(self.address)
        return balance_wei

    # Врап нативного токена
    async def wrap_native(self, amount_wei: int = None) -> str:
        """
        Оборачивает нативный токен (ETH/BNB/MATIC) в WETH/WBNB/WMATIC.
        """
        from utils.wrappers import wrap_native_token
        if amount_wei is None:
            amount_wei = self.to_wei_main(self.amount, 18)

        tx = await wrap_native_token(self.w3, self.network.name, amount_wei, self.address)
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"Отправлен wrap-тx: {tx_hash.hex()}")
        return tx_hash.hex()

    # Анврап нативного токена
    async def unwrap_native(self, amount_wei: int) -> str:
        """
        Разворачивает WETH/WBNB/... обратно в нативный токен.
        """
        from utils.wrappers import unwrap_native_token
        tx = await unwrap_native_token(self.w3, self.network.name, amount_wei, self.address)
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"Отправлен unwrap-тx: {tx_hash.hex()}")
        return tx_hash.hex()

    # Получение баланса ERC20
    async def get_erc20_balance(self) -> float | int:
        # ABI для balanceOf
        erc20_abi = [
            {
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "symbol",
                "outputs": [{"name": "", "type": "string"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.from_address), abi=erc20_abi)

        balance = await contract.functions.balanceOf(self.address).call()

        return balance

    # Создание объекта контракт для дальнейшего обращения к нему
    async def get_contract(self, contract_address: str, abi: list) -> AsyncContract:
        return self.w3.eth.contract(
            address=self.w3.to_checksum_address(contract_address), abi=abi
        )

    # Получение суммы газа за транзакцию
    async def get_tx_fee(self) -> int:
        fee_history = await self.w3.eth.fee_history(10, "latest", [50])
        base_fee = fee_history['baseFeePerGas'][-1]
        max_priority_fee = await self.w3.eth.max_priority_fee
        estimated_gas = 70_000
        max_fee_per_gas = (base_fee + max_priority_fee) * estimated_gas

        return max_fee_per_gas

    # Преобразование в веи
    def to_wei_main(self, number: int | float, decimals: int):
        unit_name = {
            6: "mwei",
            9: "gwei",
            18: "ether"
        }.get(decimals)

        if not unit_name:
            raise RuntimeError(f"Невозможно найти имя юнита с децималами: {decimals}")
        return self.w3.to_wei(number, unit_name)

    # Преобразование из веи
    def from_wei_main(self, number: int | float, decimals: int):
        unit_name = {
            6: "mwei",
            9: "gwei",
            18: "ether"
        }.get(decimals)

        if not unit_name:
            raise RuntimeError(f"Невозможно найти имя юнита с децималами: {decimals}")
        return self.w3.from_wei(number, unit_name)

    # Метод для построения транзакции на approval
    async def build_approve_tx(self, token_address: str, spender: str, amount: int) -> TxParams:
        token_address = self.w3.to_checksum_address(token_address)
        spender = self.w3.to_checksum_address(spender)
        contract = await self.get_contract(token_address, ERC20_ABI)

        tx_data = contract.encodeABI(
            fn_name="approve",
            args=[spender, amount]
        )

        tx = await self.prepare_tx()
        tx.update({
            "to": token_address,
            "data": tx_data,
            "value": 0
        })

        return tx

    # Метод для построения swap транзакции
    async def build_swap_tx(self, quote_data: dict) -> TxParams:
        """
            Строим транзакцию для обмена токенов, используя котировку.
            """
        contract_address = quote_data['contractAddress']
        amount_in = int(quote_data['srcQuoteTokenAmount'])
        amount_out_min = int(quote_data['minReceiveAmount'])

        # Строим транзакцию для обмена
        contract = await self.get_contract(contract_address, ERC20_ABI)

        tx_data = contract.encodeABI(
            fn_name="swap",
            args=[self.from_address, self.to_address, amount_in, amount_out_min]
        )

        tx = await self.prepare_tx()
        tx.update({
            "to": contract_address,
            "data": tx_data,
            "value": 0  # Если необходимо, можно добавить нативные токены
        })

        return tx

    # Подготовка транзакции
    async def prepare_tx(self, value: Union[int, float] = 0) -> TxParams:
        transaction: TxParams = {
            "chainId": await self.w3.eth.chain_id,
            "nonce": await self.w3.eth.get_transaction_count(self.address),
            "from": self.address,
            "value": self.w3.to_wei(value, "ether"),
        }

        if self.eip_1559:
            base_fee = await self.w3.eth.gas_price
            max_priority_fee_per_gas = await self.w3.eth.max_priority_fee or base_fee
            max_fee_per_gas = int(base_fee * 1.25 + max_priority_fee_per_gas)

            transaction.update({
                "maxPriorityFeePerGas": max_priority_fee_per_gas,
                "maxFeePerGas": max_fee_per_gas,
                "type": "0x2",
            })
        else:
            transaction["gasPrice"] = int((await self.w3.eth.gas_price) * 1.25)

        return transaction

    # Подпись и отправка транзакции
    async def sign_and_send_tx(self, transaction: TxParams, without_gas: bool = False):
        if not without_gas:
            transaction["gas"] = int((await self.w3.eth.estimate_gas(transaction)) * 1.5)

        signed = self.w3.eth.account.sign_transaction(transaction, self.private_key)
        signed_raw_tx = signed.raw_transaction
        logger.info("Транзакция подписана\n")

        tx_hash_bytes = await self.w3.eth.send_raw_transaction(signed_raw_tx)
        tx_hash_hex = self.w3.to_hex(tx_hash_bytes)
        logger.info("Транзакция отправлена: %s\n", tx_hash_hex)

        return tx_hash_hex

    # Ожидание результата транзакции
    async def wait_tx(self, tx_hash: Union[str, HexBytes], explorer_url: Optional[str] = None) -> bool:
        total_time = 0
        timeout = 120
        poll_latency = 10

        tx_hash_bytes = HexBytes(tx_hash)  # Приведение к HexBytes

        while True:
            try:
                receipt = await self.w3.eth.get_transaction_receipt(tx_hash_bytes)
                status = receipt.get("status")
                if status == 1:
                    logger.info(f"Транзакция выполнена успешно: {explorer_url}/tx/{tx_hash_bytes.hex()}")
                    return True
                elif status is None:
                    await asyncio.sleep(poll_latency)
                else:
                    logger.error(f"Транзакция не выполнена: {explorer_url}/tx/{tx_hash_bytes.hex()}")
                    return False
            except TransactionNotFound:
                if total_time > timeout:
                    logger.warning(f"Транзакция {tx_hash_bytes.hex()} не подтвердилась за 120 секунд")
                    return False
                total_time += poll_latency
                await asyncio.sleep(poll_latency)

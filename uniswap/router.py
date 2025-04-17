from web3 import AsyncWeb3
from web3.contract import AsyncContract
import json
import os

# Загружаем ABI один раз
with open(os.path.join("abi", "uniswap_router_v2.json"), "r", encoding="utf-8") as f:
    UNISWAP_ROUTER_ABI = json.load(f)


async def get_router_contract(w3: AsyncWeb3, router_address: str) -> AsyncContract:
    return w3.eth.contract(address=w3.to_checksum_address(router_address), abi=UNISWAP_ROUTER_ABI)


async def get_amount_out(w3: AsyncWeb3, router_address: str, amount_in_wei: int, path: list[str]) -> int:
    contract = await get_router_contract(w3, router_address)
    amounts = await contract.functions.getAmountsOut(amount_in_wei, path).call()
    return amounts[-1]  # Последний токен в цепочке — результат

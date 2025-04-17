from uniswap.router import get_amount_out
from client.client import Client
from utils.logger import logger


async def get_best_quote(clients: list[Client], wrapped_token_map: dict, usdc_map: dict) -> dict:
    """
    Проверяет котировки во всех сетях и возвращает лучшую.
    """
    best_quote = None

    for client in clients:
        try:
            amount_in_wei = client.to_wei_main(client.amount, 18)
            path = [
                client.w3.to_checksum_address(wrapped_token_map[client.network.name]),
                client.w3.to_checksum_address(usdc_map[client.network.name])
            ]

            out_amount = await get_amount_out(client.w3, client.router_address, amount_in_wei, path)

            logger.info(f"[{client.network.name}] Котировка: {client.amount} "
                        f"ETH ≈ {client.from_wei_main(out_amount, 6)} USDC")

            if not best_quote or out_amount > best_quote["usdc_amount"]:
                best_quote = {
                    "client": client,
                    "usdc_amount": out_amount,
                    "path": path
                }

        except Exception as e:
            logger.warning(f"[{client.network.name}] Ошибка получения котировки: {e}")

    return best_quote

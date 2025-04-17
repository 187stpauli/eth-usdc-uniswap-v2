import asyncio
import json
from web3 import Web3
from config.configvalidator import ConfigValidator
from client.client import Client
from uniswap.router import get_amount_out
from uniswap.swapper import swap_eth_to_usdc
from utils.logger import logger


async def main():
    try:
        logger.info("Запуск скрипта...\n")

        logger.info("Загрузка параметров конфигурации...\n")
        config = ConfigValidator("config/settings.json")
        settings = await config.validate_config()

        amount = float(settings["amount"])
        private_key = settings["private_key"]
        proxy = settings.get("proxy")
        network = settings["network"].upper()

        with open("constants/networks_data.json", "r", encoding="utf-8") as f:
            networks_data = json.load(f)

        for net in networks_data.values():
            for key in ["router_address", "wrapped_token"]:
                if key in net:
                    net[key] = Web3.to_checksum_address(net[key])

        usdc_tokens = {
            "OPTIMISM": "0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
            "BSC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
            "POLYGON": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "ARBITRUM": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        }

        net = networks_data[network]

        logger.info("Инициализация клиента...\n")
        client = Client(
            from_address=net["wrapped_token"],
            to_address=usdc_tokens[network],
            chain_id=net["chain_id"],
            rpc_url=net["rpc_url"],
            private_key=private_key,
            amount=amount,
            router_address=net["router_address"],
            explorer_url=net["explorer_url"],
            proxy=proxy
        )
        # Проверка на наличие wrapped native
        w_balance = await client.get_erc20_balance()
        w_balance = client.from_wei_main(w_balance, 18)

        if w_balance < amount:
            logger.info("⛓  Врапаем нативный токен в wrapped...\n")
            try:
                balance = await client.get_native_balance()
                gas_cost = await client.get_tx_fee()
                amount_in_wei = client.to_wei_main(client.amount, 18)

                if balance < amount_in_wei + gas_cost:
                    logger.error(f"[{client.network.name}] Недостаточно средств: баланс {client.from_wei_main(balance, 18)}")
                    return ""

                wrap_tx_hash = await client.wrap_native()
                await client.wait_tx(wrap_tx_hash, client.explorer_url)
            except Exception as e:
                logger.error(f"Ошибка при врапе токена: {e}")
                return

        path = [
            client.from_address,
            client.to_address
        ]

        logger.info("Подготовка свапа...\n")
        amount_in_wei = client.to_wei_main(client.amount, 18)

        try:
            usdc_out = await get_amount_out(client.w3, client.router_address, amount_in_wei, path)
            logger.info(f"[{network}] Котировка: {amount} ETH ≈ {client.from_wei_main(usdc_out, 6)} USDC")
        except Exception as e:
            logger.error(f"Не удалось получить котировку: {e}")
            return
        try:
            tx_hash = await swap_eth_to_usdc(client, path, usdc_out)
            if tx_hash:
                logger.info(f"✅ Swap завершён")
            else:
                logger.warning("❌ Swap не был отправлен")
        except Exception as e:
            logger.error(f"Ошибка при выполнении свапа: {e}")

    except Exception as e:
        logger.exception(f"Фатальная ошибка в main(): {e}")

if __name__ == "__main__":
    asyncio.run(main())

from utils.logger import logger
from client.client import Client


async def swap_eth_to_usdc(client: Client, path: list[str], usdc_out_min: int) -> str:
    """
    Выполняет свап ETH -> USDC через UniswapV2-подобный протокол.
    """
    try:
        logger.info(f"[{client.network.name}] Старт свапа {client.amount} ETH -> USDC")

        # Проверка баланса
        erc20_balance = await client.get_erc20_balance()
        amount_in_wei = client.to_wei_main(client.amount, 18)
        gas_cost = await client.get_tx_fee()
        native_balance = await client.get_native_balance()

        # 1. Хватает ли WETH (или WBNB/WMATIC)?
        if erc20_balance < amount_in_wei:
            logger.error(
                f"[{client.network.name}] Недостаточно WETH:"
                f" {client.from_wei_main(erc20_balance, 18)} < {client.amount}")
            return ""

        # 2. Хватает ли ETH для газа?
        if native_balance < gas_cost:
            logger.error(
                f"[{client.network.name}] Недостаточно ETH на газ:"
                f" {client.from_wei_main(native_balance, 18)} < {client.from_wei_main(gas_cost, 18)}")
            return ""

        # Сборка swapExactETHForTokens
        contract = await client.get_contract(client.router_address, abi=client.uniswap_router_abi)
        deadline = (await client.w3.eth.get_block("latest"))["timestamp"] + 1200

        tx_data = contract.encodeABI(
            fn_name="swapExactETHForTokens",
            args=[
                int(usdc_out_min * 0.99),  # minOut с учетом проскальзывания
                path,
                client.address,
                deadline
            ]
        )

        tx = await client.prepare_tx(value=client.amount)
        tx.update({
            "to": client.router_address,
            "data": tx_data
        })

        tx_hash = await client.sign_and_send_tx(tx)
        logger.info(f"[{client.network.name}] TX отправлен: {client.explorer_url}tx/{tx_hash}")
        await client.wait_tx(tx_hash, client.explorer_url)
        return tx_hash

    except Exception as e:
        logger.error(f"[{client.network.name}] Ошибка свапа: {e}")
        return ""

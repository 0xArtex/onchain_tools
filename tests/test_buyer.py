import asyncio

from app.services.buyer import BuyService


def _service_with_fake_evm_wallet():
    svc = object.__new__(BuyService)
    svc._sol_kp = None
    svc._evm_acct = object()  # stands in for a loaded EVM wallet
    return svc


def test_robinhood_buys_stay_unsupported_even_with_evm_wallet():
    # Odos doesn't route Robinhood Chain (4663), so no Buy button may render on
    # robinhood alerts and a stray buy callback must fail cleanly, not swap.
    svc = _service_with_fake_evm_wallet()
    assert svc.is_configured("base") is True
    assert svc.is_configured("bsc") is True
    assert svc.is_configured("robinhood") is False

    result = asyncio.run(svc.buy("robinhood", "0x" + "e" * 40, 50.0))
    assert result.success is False
    assert "Unsupported chain" in result.error

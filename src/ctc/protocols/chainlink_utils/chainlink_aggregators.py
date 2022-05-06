from __future__ import annotations

import asyncio
import functools
import typing

from ctc import evm
from ctc import rpc
from ctc import spec

from ctc.toolbox import search_utils

from . import chainlink_feed_metadata


async def async_get_aggregator_description(
    aggregator: spec.Address,
    provider: spec.ProviderSpec = None,
) -> str:

    function_abi: spec.FunctionABI = {
        'inputs': [],
        'name': 'description',
        'outputs': [{'internalType': 'string', 'name': '', 'type': 'string'}],
        'stateMutability': 'view',
        'type': 'function',
    }

    provider = rpc.get_provider(provider)
    return await rpc.async_eth_call(
        to_address=aggregator,
        provider=provider,
        function_abi=function_abi,
    )


async def async_get_aggregator_base_quote(
    aggregator: spec.Address,
    provider: spec.ProviderSpec = None,
) -> dict[str, str]:
    description = await async_get_aggregator_description(
        aggregator=aggregator,
        provider=provider,
    )
    try:
        base, quote = description.split(' / ')
        return {'base': base, 'quote': quote}
    except Exception:
        raise Exception(
            'could not determine base or quote from description: '
            + str(description)
        )


async def async_get_feed_aggregator_history(
    feed: str, provider: spec.ProviderSpec = None
) -> typing.Mapping[spec.Address, int]:

    feed = await chainlink_feed_metadata.async_resolve_feed_address(
        feed,
        provider=provider,
    )

    aggregators = await async_get_feed_previous_aggregators(
        feed=feed,
        provider=provider,
    )

    aggregator_start_blocks = await _async_get_aggregator_start_blocks(
        previous_aggregators=aggregators,
        feed=feed,
        provider=provider,
    )

    return aggregator_start_blocks


async def _async_get_aggregator_start_blocks(
    previous_aggregators: typing.Sequence[spec.Address],
    feed: spec.Address,
    provider: spec.ProviderSpec,
) -> typing.Mapping[spec.Address, int]:

    latest = await evm.async_get_latest_block_number(provider=provider)

    # TODO: use chainlink feed registry for this
    feed_creation_block = await evm.async_get_contract_creation_block(
        contract_address=feed,
        provider=provider,
    )

    aggregator_start_blocks = {}
    aggregator_start_blocks[previous_aggregators[0]] = feed_creation_block

    last_known_block = latest
    for next_aggregator in reversed(previous_aggregators[1:]):

        async_is_match = functools.partial(
            _async_aggregator_transition,
            feed=feed,
            next_aggregator=next_aggregator,
        )

        block = await search_utils.async_binary_search(
            async_is_match=async_is_match,
            start_index=feed_creation_block,
            end_index=last_known_block,
        )

        if block is None:
            raise Exception('could not determine start block of aggregator')

        aggregator_start_blocks[next_aggregator] = block
        last_known_block = block - 1

    return dict(
        sorted(aggregator_start_blocks.items(), key=lambda item: item[1])  # type: ignore
    )


async def _async_aggregator_transition(
    block: int,
    next_aggregator: spec.Address,
    feed: spec.Address,
) -> bool:
    return (
        next_aggregator
        == await chainlink_feed_metadata.async_get_feed_aggregator(
            feed,
            block=block,
        )
    )


async def async_get_feed_previous_aggregators(
    feed: str, provider: spec.ProviderSpec
) -> list[spec.Address]:

    feed = await chainlink_feed_metadata.async_resolve_feed_address(
        feed, provider=provider
    )

    current_phase = await rpc.async_eth_call(
        to_address=feed,
        function_name='phaseId',
    )

    coroutines = [
        rpc.async_eth_call(
            to_address=feed,
            function_name='phaseAggregators',
            function_parameters=[i],
        )
        for i in range(1, current_phase + 1)
    ]
    aggregators = await asyncio.gather(*coroutines)

    return aggregators

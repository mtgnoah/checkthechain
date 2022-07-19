from __future__ import annotations

import typing

from ctc import binary
from ctc import rpc
from ctc import spec
from ctc.toolbox import backend_utils

from .. import block_utils
from .. import abi_utils


def is_event_hash(data: spec.BinaryData) -> bool:
    try:
        binary.convert(data, 'binary')
        return len(binary) == 32
    except Exception:
        return False


def get_event_backend_functions() -> dict[
    str,
    dict[
        str,
        typing.Callable[
            ..., typing.Coroutine[typing.Any, typing.Any, spec.DataFrame]
        ],
    ],
]:
    from .event_backends import filesystem_events
    from .event_backends import node_events

    return {
        'get': {
            'filesystem': filesystem_events.async_get_events_from_filesystem,
            'download': async_download_events,
            'node': node_events.async_get_events_from_node,
        },
        'save': {
            'filesystem': filesystem_events.async_save_events_to_filesystem,
        },
    }


async def async_get_events(
    contract_address: spec.Address,
    *,
    start_block: spec.BlockNumberReference | None = None,
    end_block: spec.BlockNumberReference | None = None,
    backend_order: typing.Sequence[str] | None = None,
    keep_multiindex: bool = True,
    verbose: bool = True,
    **query: typing.Any,
) -> spec.DataFrame:

    if start_block is None:
        start_block = await block_utils.async_get_contract_creation_block(
            contract_address,
            verbose=verbose,
        )
    start_block = await block_utils.async_block_number_to_int(start_block)
    if end_block is None:
        end_block = 'latest'

    start_block, end_block = await block_utils.async_block_numbers_to_int(
        blocks=[start_block, end_block],
    )

    if backend_order is None:
        backend_order = ['filesystem', 'download']

    events = await backend_utils.async_run_on_backend(
        get_event_backend_functions()['get'],
        contract_address=contract_address,
        start_block=start_block,
        end_block=end_block,
        backend_order=backend_order,
        verbose=verbose,
        **query,
    )

    if not keep_multiindex:
        from ctc.toolbox import pd_utils

        events.index = pd_utils.keep_level(
            index=events.index, level='block_number'
        )

    return events


async def async_save_events(
    events: spec.DataFrame, **query: typing.Any
) -> spec.DataFrame:
    return await backend_utils.async_run_on_backend(
        get_event_backend_functions()['save'], events=events, **query
    )


async def async_transfer_events(
    *,
    contract_address: spec.Address,
    start_block: spec.BlockNumberReference | None = None,
    end_block: spec.BlockNumberReference | None = None,
    **query: typing.Any,
) -> spec.DataFrame:

    if start_block is not None and end_block is not None:
        start_block, end_block = await block_utils.async_block_numbers_to_int(
            blocks=[start_block, end_block],
        )
    elif start_block is not None:
        start_block = await block_utils.async_block_number_to_int(start_block)
    elif end_block is not None:
        end_block = await block_utils.async_block_number_to_int(end_block)

    result: spec.DataFrame = await backend_utils.async_transfer_backends(
        get=async_get_events,
        save=async_save_events,
        contract_address=contract_address,
        start_block=start_block,
        end_block=end_block,
        **query,
    )

    return result


async def async_download_events(
    contract_address: spec.Address,
    *,
    event_hash: str | None = None,
    event_name: str | None = None,
    event_abi: spec.EventABI | None = None,
    start_block: spec.BlockNumberReference | None = None,
    end_block: spec.BlockNumberReference | None = None,
    provider: spec.ProviderReference = None,
    verbose: bool = True,
) -> spec.DataFrame:
    from .event_backends import filesystem_events

    if event_hash is None and event_name is None and event_abi is None:
        raise Exception('must specify either event_hash or event_name')

    contract_address = contract_address.lower()

    if start_block is not None and end_block is not None:
        start_block, end_block = await block_utils.async_block_numbers_to_int(
            blocks=[start_block, end_block],
        )
    elif start_block is not None:
        start_block = await block_utils.async_block_number_to_int(start_block)
    elif end_block is not None:
        end_block = await block_utils.async_block_number_to_int(end_block)

    provider = rpc.get_provider(provider)
    network = provider['network']
    if network is None:
        raise Exception('could not determine network')

    # get event hash
    if event_hash is None:
        if event_abi is None:
            if event_name is None:
                raise Exception('must specify more event information')
            event_abi = await abi_utils.async_get_event_abi(
                contract_address=contract_address,
                event_name=event_name,
                network=network,
            )

        event_hash = binary.get_event_hash(event_abi)

    # determine what needs to be downloaded
    listed_events = filesystem_events.list_events(
        contract_address=contract_address,
        event_hash=event_hash,
    )
    downloads: list[typing.Mapping[str, typing.Any]] = []
    if listed_events is None:
        download: typing.Mapping[str, typing.Any] = {
            'start_block': start_block,
            'end_block': end_block,
        }
        downloads.append(download)
    else:

        block_range = listed_events['block_range']
        if start_block < block_range[0]:
            download = {
                'start_block': start_block,
                'end_block': block_range[0] - 1,
                'common_kwargs': {'verbose': verbose},
            }
            downloads.append(download)
        if end_block > block_range[-1]:
            download = {
                'start_block': block_range[-1] + 1,
                'end_block': end_block,
                'common_kwargs': {'verbose': verbose},
            }
            downloads.append(download)

    # perform downloads
    for download in downloads:
        await async_transfer_events(
            from_backend='node',
            to_backend='filesystem',
            event_hash=event_hash,
            event_abi=event_abi,
            contract_address=contract_address,
            provider=provider,
            **download,
        )

    # load from filesystem
    return await filesystem_events.async_get_events_from_filesystem(
        event_hash=event_hash,
        event_abi=event_abi,
        contract_address=contract_address,
        start_block=start_block,
        end_block=end_block,
        verbose=verbose,
        provider=provider,
    )


async def async_get_event_timestamps(events: spec.DataFrame) -> spec.DataFrame:

    # get block_numbers
    multi_index = 'block_number' in events.index.names
    if multi_index:
        block_numbers = events.index.get_level_values('block_number')
    else:
        block_numbers = events.index.values

    # get timestamps
    return await block_utils.async_get_block_timestamps(block_numbers)

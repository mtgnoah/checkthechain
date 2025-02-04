from __future__ import annotations

import toolcli

from .. import llama_tvls


def get_command_spec() -> toolcli.CommandSpec:
    return {
        'f': async_llama_chains_command,
        'help': 'output defi usage statistics',
        'args': [
            {
                'name': '-n',
                'type': int,
                'default': 15,
                'help': 'number of chains to display',
            },
        ],
        'examples': [
            '',
            '-n 50',
        ],
    }


async def async_llama_chains_command(n: int = 15) -> None:
    await llama_tvls.async_summarize_chains_tvls(n=n)

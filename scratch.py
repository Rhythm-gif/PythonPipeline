
import asyncio
from typing import AsyncGenerator

class AsyncGeneratorReader:
    def __init__(self, async_gen: AsyncGenerator[bytes, None]):
        self.async_gen = async_gen
        self.buffer = b""

    async def read(self, n: int = -1) -> bytes:
        if not self.buffer:
            try:
                self.buffer = await self.async_gen.__anext__()
            except StopAsyncIteration:
                return b""
        
        if n == -1 or n >= len(self.buffer):
            data, self.buffer = self.buffer, b""
            return data
            
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

async def test_gen():
    yield b"hello "
    yield b"world!"

async def main():
    reader = AsyncGeneratorReader(test_gen())
    print(await reader.read(5))
    print(await reader.read())

asyncio.run(main())


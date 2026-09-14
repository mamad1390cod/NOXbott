import asyncio
from bot.database import session_scope
from sqlalchemy import text

async def main():
    print("Testing database...")
    async with session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM users"))
        count = result.scalar()
        print(f"Users count: {count}")
        
        result = await session.execute(text("PRAGMA table_info(carts)"))
        cols = result.fetchall()
        print("Carts columns:")
        for col in cols:
            print(f"  {col}")

if __name__ == "__main__":
    asyncio.run(main())

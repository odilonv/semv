import aiosqlite
import uuid
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "semv"
DB_PATH = CONFIG_DIR / "semv.db"
        
async def init_db():
    """Creates the hidden directory and initializes the table if it doesn't exist"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS file_jobs (
                id TEXT PRIMARY KEY,
                original_path TEXT UNIQUE,
                root_scanned_dir TEXT,  -- NOUVELLE COLONNE
                status TEXT,
                ai_suggested_name TEXT,
                ai_suggested_folder TEXT,
                ai_reasoning TEXT,
                is_duplicate INTEGER DEFAULT 0,
                created_at TIMESTAMP
            )
        ''')
        await db.commit()

# On ajoute le paramètre root_dir
async def add_pending_file(file_path: str, root_dir: str) -> bool:
    """Adds a file to the pending queue. Return False if it is already present."""
    job_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute('''
                INSERT INTO file_jobs (id, original_path, root_scanned_dir, status, created_at)
                VALUES (?, ?, ?, 'PENDING', ?)
            ''', (job_id, str(file_path), str(root_dir), now))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False
    
async def get_jobs_by_status(status: str) -> list:
        """Retrieves all files with a specific status (e.g., 'REVIEW_READY')."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row # Allows accessing columns by their name
            async with db.execute('SELECT * FROM file_jobs WHERE status = ?', (status,)) as cursor:
                return await cursor.fetchall()
            
            
async def update_job_with_ai(job_id: str, suggested_name: str, suggested_folder: str, reasoning: str):
    """Updates a pending job with the AI's results and sets it to REVIEW_READY."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE file_jobs 
            SET status = 'REVIEW_READY', 
                ai_suggested_name = ?, 
                ai_suggested_folder = ?, 
                ai_reasoning = ?
            WHERE id = ?
        ''', (suggested_name, suggested_folder, reasoning, job_id))
        await db.commit()

async def mark_job_as_failed(job_id: str):
    """Marks a job as FAILED if the text extraction or AI crashes."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE file_jobs SET status = 'FAILED' WHERE id = ?", (job_id,))
        await db.commit()
        
async def update_job_status(job_id: str, new_status: str):
    """Updates the status of a job (e.g., to 'COMPLETED' or 'REJECTED')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE file_jobs SET status = ? WHERE id = ?", (new_status, job_id))
        await db.commit()
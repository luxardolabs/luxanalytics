-- Enable required PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- For similarity searches
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- For GIN indexes on multiple columns

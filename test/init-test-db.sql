-- Initialize test database with sample data
-- This script runs automatically when the PostgreSQL container starts

-- Create a test table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create another test table
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert sample data
INSERT INTO users (username, email) VALUES
    ('alice', 'alice@example.com'),
    ('bob', 'bob@example.com'),
    ('charlie', 'charlie@example.com')
ON CONFLICT (username) DO NOTHING;

INSERT INTO products (name, price, description) VALUES
    ('Laptop', 999.99, 'High-performance laptop'),
    ('Mouse', 29.99, 'Wireless mouse'),
    ('Keyboard', 79.99, 'Mechanical keyboard')
ON CONFLICT DO NOTHING;

-- Create a view
CREATE OR REPLACE VIEW user_count AS
SELECT COUNT(*) as total_users FROM users;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO testuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO testuser;

-- Print confirmation
DO $$
BEGIN
    RAISE NOTICE 'Test database initialized successfully!';
    RAISE NOTICE 'Tables: users, products';
    RAISE NOTICE 'Sample data inserted';
END $$;


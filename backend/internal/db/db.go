package db

import (
	"database/sql"
	_ "github.com/jackc/pgx/v5/stdlib"
)

func New(dsn string) (*sql.DB, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(5)
	return db, db.Ping()
}

func Migrate(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS users (
			id            TEXT   PRIMARY KEY,
			email         TEXT   UNIQUE NOT NULL,
			password_hash TEXT   NOT NULL,
			role          TEXT   NOT NULL DEFAULT 'annotator',
			status        TEXT   NOT NULL DEFAULT 'pending',
			created_at    BIGINT NOT NULL,
			updated_at    BIGINT NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_users_email  ON users(email);
		CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
		CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
	`)
	return err
}

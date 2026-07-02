package admin

import (
	"database/sql"
	"errors"
	"fmt"
	"time"

	"human-archive/backend/internal/models"
)

var ErrNotFound = errors.New("user not found")

type Service struct {
	db *sql.DB
}

func NewService(db *sql.DB) *Service {
	return &Service{db: db}
}

func (s *Service) ListUsers(role, status string) ([]*models.User, error) {
	q := `SELECT id, email, role, status, created_at, updated_at FROM users WHERE 1=1`
	args := []any{}
	i := 1

	if role != "" {
		q += fmt.Sprintf(" AND role = $%d", i)
		args = append(args, role)
		i++
	}
	if status != "" {
		q += fmt.Sprintf(" AND status = $%d", i)
		args = append(args, status)
		i++
	}
	_ = i
	q += " ORDER BY created_at DESC"

	rows, err := s.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var users []*models.User
	for rows.Next() {
		u, err := scanUser(rows)
		if err != nil {
			return nil, err
		}
		users = append(users, u)
	}
	return users, rows.Err()
}

func (s *Service) SetStatus(id string, status models.UserStatus) (*models.User, error) {
	res, err := s.db.Exec(
		`UPDATE users SET status = $1, updated_at = $2 WHERE id = $3 AND role = 'annotator'`,
		status, time.Now().Unix(), id,
	)
	if err != nil {
		return nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, ErrNotFound
	}

	var createdUnix, updatedUnix int64
	u := &models.User{}
	err = s.db.QueryRow(
		`SELECT id, email, role, status, created_at, updated_at FROM users WHERE id = $1`, id,
	).Scan(&u.ID, &u.Email, &u.Role, &u.Status, &createdUnix, &updatedUnix)
	if err != nil {
		return nil, err
	}
	u.CreatedAt = time.Unix(createdUnix, 0).UTC()
	u.UpdatedAt = time.Unix(updatedUnix, 0).UTC()
	return u, nil
}

func (s *Service) DeleteUser(id string) error {
	res, err := s.db.Exec(`DELETE FROM users WHERE id = $1 AND role = 'annotator'`, id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanUser(r rowScanner) (*models.User, error) {
	u := &models.User{}
	var createdUnix, updatedUnix int64
	if err := r.Scan(&u.ID, &u.Email, &u.Role, &u.Status, &createdUnix, &updatedUnix); err != nil {
		return nil, err
	}
	u.CreatedAt = time.Unix(createdUnix, 0).UTC()
	u.UpdatedAt = time.Unix(updatedUnix, 0).UTC()
	return u, nil
}

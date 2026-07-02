package auth

import (
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"

	"human-archive/backend/internal/models"
	"human-archive/backend/internal/token"
)

var (
	ErrEmailTaken   = errors.New("email already registered")
	ErrInvalidCreds = errors.New("invalid credentials")
	ErrNotApproved  = errors.New("account pending approval")
	ErrRejected     = errors.New("account rejected")
)

type Service struct {
	db        *sql.DB
	jwtSecret string
}

func NewService(db *sql.DB, jwtSecret string) *Service {
	return &Service{db: db, jwtSecret: jwtSecret}
}

func (s *Service) Register(email, password string) (*models.User, error) {
	email = strings.ToLower(strings.TrimSpace(email))

	var exists int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM users WHERE email = $1", email).Scan(&exists); err != nil {
		return nil, err
	}
	if exists > 0 {
		return nil, ErrEmailTaken
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return nil, err
	}

	now := time.Now().UTC()
	user := &models.User{
		ID:        uuid.New().String(),
		Email:     email,
		Role:      models.RoleAnnotator,
		Status:    models.StatusPending,
		CreatedAt: now,
		UpdatedAt: now,
	}

	_, err = s.db.Exec(
		`INSERT INTO users (id, email, password_hash, role, status, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7)`,
		user.ID, user.Email, string(hash), user.Role, user.Status,
		user.CreatedAt.Unix(), user.UpdatedAt.Unix(),
	)
	return user, err
}

func (s *Service) Login(email, password string) (string, *models.User, error) {
	email = strings.ToLower(strings.TrimSpace(email))

	var hash string
	var createdUnix, updatedUnix int64
	user := &models.User{}

	err := s.db.QueryRow(
		`SELECT id, email, password_hash, role, status, created_at, updated_at
		 FROM users WHERE email = $1`, email,
	).Scan(&user.ID, &user.Email, &hash, &user.Role, &user.Status, &createdUnix, &updatedUnix)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil, ErrInvalidCreds
	}
	if err != nil {
		return "", nil, err
	}

	if bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) != nil {
		return "", nil, ErrInvalidCreds
	}

	switch user.Status {
	case models.StatusPending:
		return "", nil, ErrNotApproved
	case models.StatusRejected:
		return "", nil, ErrRejected
	}

	user.CreatedAt = time.Unix(createdUnix, 0).UTC()
	user.UpdatedAt = time.Unix(updatedUnix, 0).UTC()

	tok, err := token.Issue(user.ID, user.Role, s.jwtSecret)
	return tok, user, err
}

func (s *Service) FindByID(id string) (*models.User, error) {
	user := &models.User{}
	var createdUnix, updatedUnix int64

	err := s.db.QueryRow(
		`SELECT id, email, role, status, created_at, updated_at FROM users WHERE id = $1`, id,
	).Scan(&user.ID, &user.Email, &user.Role, &user.Status, &createdUnix, &updatedUnix)
	if err != nil {
		return nil, err
	}
	user.CreatedAt = time.Unix(createdUnix, 0).UTC()
	user.UpdatedAt = time.Unix(updatedUnix, 0).UTC()
	return user, nil
}

func SeedAdmin(db *sql.DB, email, password string) error {
	var count int
	if err := db.QueryRow("SELECT COUNT(*) FROM users WHERE role = 'admin'").Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return nil
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return err
	}

	now := time.Now().Unix()
	_, err = db.Exec(
		`INSERT INTO users (id, email, password_hash, role, status, created_at, updated_at)
		 VALUES ($1, $2, $3, 'admin', 'active', $4, $5)`,
		uuid.New().String(), strings.ToLower(strings.TrimSpace(email)), string(hash), now, now,
	)
	return err
}

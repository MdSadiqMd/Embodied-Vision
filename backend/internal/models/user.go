package models

import "time"

type UserRole   string
type UserStatus string

const (
	RoleAnnotator UserRole = "annotator"
	RoleAdmin     UserRole = "admin"
)

const (
	StatusPending  UserStatus = "pending"
	StatusActive   UserStatus = "active"
	StatusRejected UserStatus = "rejected"
)

type User struct {
	ID        string     `json:"id"`
	Email     string     `json:"email"`
	Role      UserRole   `json:"role"`
	Status    UserStatus `json:"status"`
	CreatedAt time.Time  `json:"created_at"`
	UpdatedAt time.Time  `json:"updated_at"`
}

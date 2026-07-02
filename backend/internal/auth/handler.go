package auth

import (
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"human-archive/backend/internal/middleware"
)

type Handler struct {
	svc *Service
}

func NewHandler(svc *Service) *Handler {
	return &Handler{svc: svc}
}

func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonErr(w, "invalid request body", http.StatusBadRequest)
		return
	}
	if req.Email == "" || req.Password == "" {
		jsonErr(w, "email and password required", http.StatusBadRequest)
		return
	}
	if !strings.Contains(req.Email, "@") {
		jsonErr(w, "invalid email", http.StatusBadRequest)
		return
	}
	if len(req.Password) < 8 {
		jsonErr(w, "password must be at least 8 characters", http.StatusBadRequest)
		return
	}

	user, err := h.svc.Register(req.Email, req.Password)
	if errors.Is(err, ErrEmailTaken) {
		jsonErr(w, "email already registered", http.StatusConflict)
		return
	}
	if err != nil {
		jsonErr(w, "registration failed", http.StatusInternalServerError)
		return
	}

	jsonOK(w, map[string]any{
		"message": "registration successful, awaiting admin approval",
		"user":    user,
	}, http.StatusCreated)
}

func (h *Handler) Login(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonErr(w, "invalid request body", http.StatusBadRequest)
		return
	}

	tok, user, err := h.svc.Login(req.Email, req.Password)
	switch {
	case errors.Is(err, ErrInvalidCreds):
		jsonErr(w, "invalid email or password", http.StatusUnauthorized)
	case errors.Is(err, ErrNotApproved):
		jsonErr(w, "account pending admin approval", http.StatusForbidden)
	case errors.Is(err, ErrRejected):
		jsonErr(w, "account has been rejected", http.StatusForbidden)
	case err != nil:
		jsonErr(w, "login failed", http.StatusInternalServerError)
	default:
		jsonOK(w, map[string]any{"token": tok, "user": user}, http.StatusOK)
	}
}

func (h *Handler) Me(w http.ResponseWriter, r *http.Request) {
	id := middleware.UserIDFromContext(r.Context())
	user, err := h.svc.FindByID(id)
	if errors.Is(err, sql.ErrNoRows) {
		jsonErr(w, "user not found", http.StatusNotFound)
		return
	}
	if err != nil {
		jsonErr(w, "failed to fetch user", http.StatusInternalServerError)
		return
	}
	jsonOK(w, user, http.StatusOK)
}

func (h *Handler) Logout(w http.ResponseWriter, r *http.Request) {
	jsonOK(w, map[string]string{"message": "logged out"}, http.StatusOK)
}

func jsonOK(w http.ResponseWriter, v any, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func jsonErr(w http.ResponseWriter, msg string, status int) {
	jsonOK(w, map[string]string{"error": msg}, status)
}

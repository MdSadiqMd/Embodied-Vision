package admin

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	"human-archive/backend/internal/models"
)

type Handler struct {
	svc *Service
}

func NewHandler(svc *Service) *Handler {
	return &Handler{svc: svc}
}

func (h *Handler) ListUsers(w http.ResponseWriter, r *http.Request) {
	role := r.URL.Query().Get("role")
	status := r.URL.Query().Get("status")

	users, err := h.svc.ListUsers(role, status)
	if err != nil {
		jsonErr(w, "failed to list users", http.StatusInternalServerError)
		return
	}
	if users == nil {
		users = []*models.User{}
	}
	jsonOK(w, users, http.StatusOK)
}

func (h *Handler) ListPending(w http.ResponseWriter, r *http.Request) {
	users, err := h.svc.ListUsers("annotator", "pending")
	if err != nil {
		jsonErr(w, "failed to list pending users", http.StatusInternalServerError)
		return
	}
	if users == nil {
		users = []*models.User{}
	}
	jsonOK(w, users, http.StatusOK)
}

func (h *Handler) ApproveUser(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	user, err := h.svc.SetStatus(id, models.StatusActive)
	if errors.Is(err, ErrNotFound) {
		jsonErr(w, "user not found", http.StatusNotFound)
		return
	}
	if err != nil {
		jsonErr(w, "failed to approve user", http.StatusInternalServerError)
		return
	}
	jsonOK(w, user, http.StatusOK)
}

func (h *Handler) RejectUser(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	user, err := h.svc.SetStatus(id, models.StatusRejected)
	if errors.Is(err, ErrNotFound) {
		jsonErr(w, "user not found", http.StatusNotFound)
		return
	}
	if err != nil {
		jsonErr(w, "failed to reject user", http.StatusInternalServerError)
		return
	}
	jsonOK(w, user, http.StatusOK)
}

func (h *Handler) DeleteUser(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	err := h.svc.DeleteUser(id)
	if errors.Is(err, ErrNotFound) {
		jsonErr(w, "user not found", http.StatusNotFound)
		return
	}
	if err != nil {
		jsonErr(w, "failed to delete user", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func jsonOK(w http.ResponseWriter, v any, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func jsonErr(w http.ResponseWriter, msg string, status int) {
	jsonOK(w, map[string]string{"error": msg}, status)
}

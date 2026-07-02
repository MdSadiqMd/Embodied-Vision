package middleware

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"human-archive/backend/internal/config"
	"human-archive/backend/internal/models"
	"human-archive/backend/internal/token"
)

type ctxKey string

const (
	ctxUserID ctxKey = "userID"
	ctxRole   ctxKey = "role"
)

func RequireAuth(cfg *config.Config) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			h := r.Header.Get("Authorization")
			if !strings.HasPrefix(h, "Bearer ") {
				jsonErr(w, "missing or invalid authorization header", http.StatusUnauthorized)
				return
			}

			claims, err := token.Parse(strings.TrimPrefix(h, "Bearer "), cfg.JWTSecret)
			if err != nil {
				jsonErr(w, "invalid or expired token", http.StatusUnauthorized)
				return
			}

			ctx := context.WithValue(r.Context(), ctxUserID, claims.UserID)
			ctx = context.WithValue(ctx, ctxRole, claims.Role)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

func RequireAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if RoleFromContext(r.Context()) != models.RoleAdmin {
			jsonErr(w, "admin access required", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func UserIDFromContext(ctx context.Context) string {
	id, _ := ctx.Value(ctxUserID).(string)
	return id
}

func RoleFromContext(ctx context.Context) models.UserRole {
	role, _ := ctx.Value(ctxRole).(models.UserRole)
	return role
}

func jsonErr(w http.ResponseWriter, msg string, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

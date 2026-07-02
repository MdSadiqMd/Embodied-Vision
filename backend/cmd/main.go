package main

import (
	"log"
	"net/http"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"

	"human-archive/backend/internal/admin"
	"human-archive/backend/internal/auth"
	"human-archive/backend/internal/config"
	"human-archive/backend/internal/db"
	"human-archive/backend/internal/middleware"
)

func main() {
	cfg := config.Load()

	database, err := db.New(cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer database.Close()

	if err := db.Migrate(database); err != nil {
		log.Fatalf("db migrate: %v", err)
	}

	if cfg.AdminPassword != "" {
		if err := auth.SeedAdmin(database, cfg.AdminEmail, cfg.AdminPassword); err != nil {
			log.Fatalf("seed admin: %v", err)
		}
	} else {
		var count int
		database.QueryRow("SELECT COUNT(*) FROM users WHERE role = 'admin'").Scan(&count)
		if count == 0 {
			log.Fatal("no admin exists and ADMIN_PASSWORD is not set")
		}
	}

	authSvc := auth.NewService(database, cfg.JWTSecret)
	adminSvc := admin.NewService(database)

	authH := auth.NewHandler(authSvc)
	adminH := admin.NewHandler(adminSvc)

	r := chi.NewRouter()
	r.Use(chimw.Logger)
	r.Use(chimw.Recoverer)
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins: []string{"*"},
		AllowedMethods: []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowedHeaders: []string{"Accept", "Authorization", "Content-Type"},
		MaxAge:         300,
	}))

	r.Route("/auth", func(r chi.Router) {
		r.Post("/register", authH.Register)
		r.Post("/login", authH.Login)
		r.Group(func(r chi.Router) {
			r.Use(middleware.RequireAuth(cfg))
			r.Get("/me", authH.Me)
			r.Post("/logout", authH.Logout)
		})
	})

	r.Route("/admin", func(r chi.Router) {
		r.Use(middleware.RequireAuth(cfg))
		r.Use(middleware.RequireAdmin)
		r.Get("/users", adminH.ListUsers)
		r.Get("/users/pending", adminH.ListPending)
		r.Post("/users/{id}/approve", adminH.ApproveUser)
		r.Post("/users/{id}/reject", adminH.RejectUser)
		r.Delete("/users/{id}", adminH.DeleteUser)
	})

	log.Printf("listening on :%s", cfg.Port)
	log.Fatal(http.ListenAndServe(":"+cfg.Port, r))
}

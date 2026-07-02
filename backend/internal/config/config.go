package config

import (
	"log"
	"os"
)

type Config struct {
	Port          string
	DatabaseURL   string
	JWTSecret     string
	AdminEmail    string
	AdminPassword string
}

func Load() *Config {
	return &Config{
		Port:          getEnv("PORT", "8080"),
		DatabaseURL:   mustEnv("DATABASE_URL"),
		JWTSecret:     mustEnv("JWT_SECRET"),
		AdminEmail:    getEnv("ADMIN_EMAIL", "admin@example.com"),
		AdminPassword: os.Getenv("ADMIN_PASSWORD"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func mustEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("required env var %s not set", key)
	}
	return v
}

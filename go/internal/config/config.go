package config

import (
	"log/slog"
	"os"
	"strconv"
)

// Config holds all application configuration loaded from environment variables.
type Config struct {
	Port        int
	LogLevelStr string

	// Infrastructure
	QdrantURL        string
	ElasticsearchURL string
	Neo4jURI         string
	PostgresURL      string
	RedisURL         string

	// Sidecars
	EmbeddingURL    string
	CrossEncoderURL string
	FaissURL        string
}

// Load reads configuration from environment variables with sensible defaults.
func Load() *Config {
	return &Config{
		Port:             envInt("PORT", 8080),
		LogLevelStr:      envStr("LOG_LEVEL", "info"),
		QdrantURL:        envStr("QDRANT_URL", "http://localhost:6333"),
		ElasticsearchURL: envStr("ELASTICSEARCH_URL", "http://localhost:9200"),
		Neo4jURI:         envStr("NEO4J_URI", "bolt://localhost:7687"),
		PostgresURL:      envStr("POSTGRES_URL", "postgres://ragstack:ragstack@localhost:5432/ragstack"),
		RedisURL:         envStr("REDIS_URL", "redis://localhost:6379"),
		EmbeddingURL:     envStr("EMBEDDING_SIDECAR_URL", "http://localhost:50053"),
		CrossEncoderURL:  envStr("CROSSENCODER_SIDECAR_URL", "http://localhost:50052"),
		FaissURL:         envStr("FAISS_SIDECAR_URL", "http://localhost:50051"),
	}
}

// LogLevel returns the slog.Level corresponding to the configured log level string.
func (c *Config) LogLevel() slog.Level {
	switch c.LogLevelStr {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

func envStr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

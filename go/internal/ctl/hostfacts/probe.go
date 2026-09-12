package hostfacts

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ProbeTimeout bounds every store/API probe. The plan fixes it at 2 s: a
// dashboard poll must never hang on a wedged store.
const ProbeTimeout = 2 * time.Second

// maxProbeBody caps what a probe reads. A listing endpoint that answers
// megabytes is a store problem, not something to buffer.
const maxProbeBody = 1 << 20

// HTTPProber is the live Prober: GET only, redirects refused, 2 s, no
// credentials. Nothing here ever sends an API key — PR-A has no deep-health
// probe for exactly that reason.
type HTTPProber struct {
	Client *http.Client
}

var _ Prober = (*HTTPProber)(nil)

// NewProber returns an HTTPProber with the contract's timeout and a client
// that returns the redirect response itself instead of following it (a
// redirect is an answer about the origin, never something to chase).
func NewProber() *HTTPProber {
	return &HTTPProber{Client: &http.Client{
		Timeout: ProbeTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}}
}

// Probe GETs url and returns its status code.
func (p *HTTPProber) Probe(ctx context.Context, url string) (int, error) {
	status, _, err := p.get(ctx, url)
	return status, err
}

func (p *HTTPProber) get(ctx context.Context, url string) (int, []byte, error) {
	ctx, cancel := context.WithTimeout(ctx, ProbeTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Accept", "application/json")
	client := p.Client
	if client == nil {
		client = NewProber().Client
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxProbeBody))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	return resp.StatusCode, body, nil
}

// QdrantCollections lists <url>/collections — names only, no points, no
// payloads. Used for the fleet health column.
func (p *HTTPProber) QdrantCollections(ctx context.Context, url string) (StoreListing, error) {
	status, body, err := p.get(ctx, strings.TrimSuffix(url, "/")+"/collections")
	out := StoreListing{Status: status}
	if err != nil {
		return out, err
	}
	if status != http.StatusOK {
		return out, fmt.Errorf("qdrant %s/collections: status %d", url, status)
	}
	var parsed struct {
		Result struct {
			Collections []struct {
				Name string `json:"name"`
			} `json:"collections"`
		} `json:"result"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return out, fmt.Errorf("qdrant %s/collections: %w", url, err)
	}
	for _, c := range parsed.Result.Collections {
		out.Names = append(out.Names, c.Name)
	}
	out.Count = len(out.Names)
	return out, nil
}

// ESIndices lists <url>/_cat/indices — index names only.
func (p *HTTPProber) ESIndices(ctx context.Context, url string) (StoreListing, error) {
	status, body, err := p.get(ctx, strings.TrimSuffix(url, "/")+"/_cat/indices?format=json&h=index")
	out := StoreListing{Status: status}
	if err != nil {
		return out, err
	}
	if status != http.StatusOK {
		return out, fmt.Errorf("elasticsearch %s/_cat/indices: status %d", url, status)
	}
	var parsed []struct {
		Index string `json:"index"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return out, fmt.Errorf("elasticsearch %s/_cat/indices: %w", url, err)
	}
	for _, i := range parsed {
		out.Names = append(out.Names, i.Index)
	}
	out.Count = len(out.Names)
	return out, nil
}

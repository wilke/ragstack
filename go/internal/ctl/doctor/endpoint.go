package doctor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// allowedEndpointHosts is Options.AllowedEndpointHosts, else the process
// environment's CTL_ALLOWED_ENDPOINT_HOSTS, else the value in ctl.env (the
// CLI's doctor run through ctl-as-svc.sh does not load that file, and a doctor
// that disagreed with the daemon's about the allowlist would be two doctors),
// else the default. A malformed value is reported once, as the finding's
// detail, and the default is used.
func (d *run) allowedEndpointHosts() ([]string, string) {
	if d.opts.AllowedEndpointHosts != nil {
		return d.opts.AllowedEndpointHosts, ""
	}
	fromFile := ""
	if b, err := os.ReadFile(filepath.Join(d.roots.CtlConfigDir, "ctl.env")); err == nil {
		if f, _, perr := envfile.ParseLenient(b); perr == nil {
			fromFile, _ = f.Get(settings.EnvAllowedEndpointHosts)
		}
	}
	hosts, err := settings.AllowedHostsFrom(fromFile)
	if err != nil {
		host, _ := os.Hostname()
		return settings.DefaultAllowedHosts(host), err.Error()
	}
	return hosts, ""
}

// endpointHostCheck raises endpoint_host_not_allowed (warn) for every
// URL-valued executable-surface setting of t whose host is outside the
// allowlist. The values come from tenant.env — the file the API's environment
// is built from — with settings{} as the fallback for a key the file does not
// name (surface keys never reach settings{} on a host, but a fixture may).
func (d *run) endpointHostCheck(t *registry.Tenant) {
	vals := map[string]string{}
	for k, v := range t.Settings {
		vals[k] = v
	}
	if b, err := os.ReadFile(filepath.Join(t.DataDir, "config", "tenant.env")); err == nil {
		if f, _, perr := envfile.ParseLenient(b); perr == nil {
			for _, a := range f.Assignments() {
				vals[a.Key] = a.Value
			}
		}
	}
	allowed, problem := d.allowedEndpointHosts()
	ok := map[string]bool{}
	for _, h := range allowed {
		ok[h] = true
	}
	for _, key := range settings.SurfaceURLKeys() {
		v := strings.TrimSpace(vals[key])
		if v == "" {
			continue
		}
		seen := map[string]bool{}
		for _, h := range settings.SurfaceURLHosts(key, v) {
			if ok[h] || seen[h] {
				continue
			}
			seen[h] = true
			detail := fmt.Sprintf("%s names host %q, which is not in %s [%s]: `env set-surface` would refuse this "+
				"value today. Add the host to ctl.env if it is one the operator means to allow, or repoint the "+
				"setting", key, h, settings.EnvAllowedEndpointHosts, strings.Join(allowed, ","))
			if problem != "" {
				detail += " (the configured allowlist is malformed and the default is in force: " + problem + ")"
			}
			d.add(model.LevelWarn, EndpointHostNotAllowed, t.Name, detail)
		}
	}
}

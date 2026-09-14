package registry

import (
	"strings"
	"testing"
)

// TestContractRefusesTheUIModePortContradiction.
//
// mode and port are not independent fields, and checking them one at a time
// let the contradiction through in both directions: a `static` row carrying a
// port describes a Vite dev server the renderer never emits a map row for
// (nginx serves that tenant from <data_dir>/ui/dist instead), and a `dev` row
// WITHOUT one is a gateway file render.NginxTenants refuses for the whole
// fleet, not just this tenant.
func TestContractRefusesTheUIModePortContradiction(t *testing.T) {
	for _, tc := range []struct {
		name string
		ui   UI
		want string
	}{
		{"static with a port", UI{Mode: UIModeStatic, Port: NullPort(5210), Base: "/ragstack/dev/ui/"}, "null port"},
		{"dev without a port", UI{Mode: UIModeDev, Base: "/ragstack/dev/ui/"}, "needs a port"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := LiveFixture()
			f.UpdatedBy, f.UpdatedAt = "local:test", "2026-09-14T00:00:00Z"
			f.Tenants["dev"].UI = tc.ui
			err := f.ValidateContract()
			if err == nil {
				t.Fatalf("%+v was accepted", tc.ui)
			}
			if !strings.Contains(err.Error(), "/tenants/dev/ui/port") || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("the error does not point at the field and say why: %v", err)
			}
		})
	}

	// The legal pairs still pass.
	for _, ui := range []UI{
		{Mode: UIModeStatic, Base: "/ragstack/dev/ui/"},
		{Mode: UIModeDev, Port: NullPort(8090), Base: "/ragstack/dev/ui/"},
		{Mode: UIModeExternal, Base: "/ragstack/dev/ui/"},
		{Mode: UIModeExternal, Port: NullPort(5175), Base: "/ragstack/dev/ui/"},
	} {
		f := LiveFixture()
		f.UpdatedBy, f.UpdatedAt = "local:test", "2026-09-14T00:00:00Z"
		f.Tenants["dev"].UI = ui
		if err := f.ValidateContract(); err != nil {
			t.Errorf("%+v was refused: %v", ui, err)
		}
	}
}

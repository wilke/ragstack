package drivers

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// keyFlag is crontab(1)'s response key: the argv is one word, `-l` or `-`.
const keyFlag = "key=\"$1\"\n"

func newCrontab(t *testing.T) (*RealCrontab, *stub) {
	t.Helper()
	s := newStub(t, keyFlag)
	return &RealCrontab{run: &runner{}, Bin: s.Path}, s
}

const crontabBody = `# m h  dom mon dow   command
@reboot /rag/bin/start-proxy.sh
*/5 * * * * /rag/bin/ragstack-ctl fleet start --all --direct # ragstack-ctl boot
`

func TestCrontabListReadsTheCurrentAccountsCrontab(t *testing.T) {
	d, s := newCrontab(t)
	s.respond("-l", crontabBody, "", 0)
	got, err := d.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != crontabBody {
		t.Errorf("List = %q, want the body crontab printed", got)
	}
	// One word of argv, and no `-u`: this driver reads the crontab of the
	// account it runs as and of no other.
	if argv := s.argv(); len(argv) != 1 || argv[0] != "-l" {
		t.Fatalf("List ran %v, want exactly `-l`", argv)
	}
}

// TestCrontabListOfAnAccountWithNoCrontabIsEmpty is the state `fleet
// enable-boot` runs in on a freshly created service account: crontab(1) exits
// 1 and says so on stderr, and a driver that passed that through would make a
// host that has never had a crontab indistinguishable from a broken cron.
func TestCrontabListOfAnAccountWithNoCrontabIsEmpty(t *testing.T) {
	d, s := newCrontab(t)
	s.respond("-l", "", "no crontab for svcbvbrc\n", 1)
	got, err := d.List(context.Background())
	if err != nil {
		t.Fatalf("List with no crontab = %v, want no error", err)
	}
	if len(got) != 0 {
		t.Errorf("List with no crontab = %q, want an empty body", got)
	}
}

// TestCrontabListPassesOnARealFailure is the other half: crontab(1) exits 1
// for a dozen reasons, and treating every one of them as "no crontab" would
// let `enable-boot` overwrite a crontab it had failed to read.
func TestCrontabListPassesOnARealFailure(t *testing.T) {
	d, s := newCrontab(t)
	s.respond("-l", "", "crontab: cannot open /var/spool/cron/crontabs: permission denied\n", 1)
	if _, err := d.List(context.Background()); err == nil {
		t.Fatal("List of an unreadable crontab = nil, want the failure")
	}
}

func TestCrontabSetWritesTheBodyOnStdin(t *testing.T) {
	d, s := newCrontab(t)
	if err := d.Set(context.Background(), []byte(crontabBody)); err != nil {
		t.Fatal(err)
	}
	// `-`, never a file: a file the ctl wrote and crontab(1) then read is a
	// window in which anything that can write that path chooses the account's
	// cron jobs.
	if argv := s.argv(); len(argv) != 1 || argv[0] != "-" {
		t.Fatalf("Set ran %v, want exactly `-`", argv)
	}
	if got := s.stdinOf("-"); got != crontabBody {
		t.Errorf("crontab read %q on stdin, want the body", got)
	}
}

// TestCrontabSetOfAnEmptyBodyClearsTheCrontab: `--no-cron` on an account whose
// only line was the ctl's leaves nothing, and nothing is a legitimate crontab.
func TestCrontabSetOfAnEmptyBodyClearsTheCrontab(t *testing.T) {
	d, s := newCrontab(t)
	if err := d.Set(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if got := s.stdinOf("-"); got != "" {
		t.Errorf("crontab read %q on stdin, want nothing", got)
	}
}

func TestCrontabSetRefusesABodyThatIsNotACrontab(t *testing.T) {
	ctx := context.Background()
	for what, body := range map[string][]byte{
		"a NUL":      []byte("@reboot /rag/bin/ctl\x00\n"),
		"64 KiB + 1": []byte(strings.Repeat("# filler\n", (maxCrontab/9)+64)),
	} {
		d, s := newCrontab(t)
		if err := d.Set(ctx, body); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Set of a body with %s = %v, want a refusal", what, err)
		}
		s.ranNothing("a body that is not a crontab")
	}
}

// TestCrontabMatchesTheFake: the fake claims an account with no crontab reads
// as an empty body, and that Set replaces the whole thing. Both halves have to
// be true of the host, or an op tested against the fake would behave
// differently on coconut.
func TestCrontabMatchesTheFake(t *testing.T) {
	ctx := context.Background()
	fake := NewFake(FakeOptions{}).FakeCrontab()
	real, s := newCrontab(t)
	s.respond("-l", "", "no crontab for svcbvbrc\n", 1)

	fakeBody, fakeErr := fake.List(ctx)
	realBody, realErr := real.List(ctx)
	if fakeErr != nil || realErr != nil {
		t.Fatalf("List with no crontab: fake = %v, real = %v; both must succeed", fakeErr, realErr)
	}
	if len(fakeBody) != 0 || len(realBody) != 0 {
		t.Errorf("List with no crontab: fake = %q, real = %q; both must be empty", fakeBody, realBody)
	}

	if err := fake.Set(ctx, []byte(crontabBody)); err != nil {
		t.Fatal(err)
	}
	if err := real.Set(ctx, []byte(crontabBody)); err != nil {
		t.Fatal(err)
	}
	if string(fake.Body) != crontabBody {
		t.Errorf("the fake kept %q", fake.Body)
	}
	if got := s.stdinOf("-"); got != crontabBody {
		t.Errorf("the host was given %q", got)
	}
}

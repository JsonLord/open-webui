package auth

import (
	"os"
	"path/filepath"
	"testing"

	"plandex-cli/fs"
	"plandex-cli/types"
	shared "plandex-shared"
)

type localAuthClient struct {
	types.ApiClient
	hasAccount bool
	created    int
	signedIn   int
}

func (c *localAuthClient) CreateEmailVerification(email, host, userId string) (*shared.CreateEmailVerificationResponse, *shared.ApiError) {
	return &shared.CreateEmailVerificationResponse{HasAccount: c.hasAccount, IsLocalMode: true}, nil
}

func (c *localAuthClient) CreateAccount(req shared.CreateAccountRequest, host string) (*shared.SessionResponse, *shared.ApiError) {
	c.created++
	return localSession(), nil
}

func (c *localAuthClient) SignIn(req shared.SignInRequest, host string) (*shared.SessionResponse, *shared.ApiError) {
	c.signedIn++
	return localSession(), nil
}

func (c *localAuthClient) GetOrgSession() (*shared.Org, *shared.ApiError) {
	return &shared.Org{Id: "org-id", Name: "Local Org"}, nil
}

func localSession() *shared.SessionResponse {
	return &shared.SessionResponse{
		UserId: "user-id", Token: "sensitive-token", Email: "local-admin@plandex.ai",
		UserName: "Local Admin", IsLocalMode: true,
		Orgs: []*shared.Org{{Id: "org-id", Name: "Local Org"}},
	}
}

func configureAuthPaths(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	fs.HomeAccountsPath = filepath.Join(dir, "accounts.json")
	fs.HomeAuthPath = filepath.Join(dir, "auth.json")
	Current = nil
	t.Cleanup(func() { Current = nil })
}

func TestSignInLocalCreatesAccountWithoutPrompt(t *testing.T) {
	configureAuthPaths(t)
	client := &localAuthClient{}
	SetApiClient(client)
	if err := SignInLocal("http://127.0.0.1:8099"); err != nil {
		t.Fatal(err)
	}
	if client.created != 1 || client.signedIn != 0 {
		t.Fatalf("unexpected calls: create=%d sign-in=%d", client.created, client.signedIn)
	}
	assertPrivateAuthFiles(t)
}

func TestSignInLocalUsesExistingAccount(t *testing.T) {
	configureAuthPaths(t)
	client := &localAuthClient{hasAccount: true}
	SetApiClient(client)
	if err := SignInLocal("http://127.0.0.1:8099"); err != nil {
		t.Fatal(err)
	}
	if client.created != 0 || client.signedIn != 1 {
		t.Fatalf("unexpected calls: create=%d sign-in=%d", client.created, client.signedIn)
	}
	assertPrivateAuthFiles(t)
}

func TestValidateLocalAuthUsesPersistedNativeState(t *testing.T) {
	configureAuthPaths(t)
	client := &localAuthClient{}
	SetApiClient(client)
	if err := handleSignInResponse(localSession(), "http://127.0.0.1:8099"); err != nil {
		t.Fatal(err)
	}
	Current = nil
	if err := ValidateLocalAuth("http://127.0.0.1:8099"); err != nil {
		t.Fatal(err)
	}
	if Current == nil || Current.OrgId != "org-id" {
		t.Fatal("persisted local auth was not restored")
	}
}

func TestSignInLocalRejectsNonLocalServer(t *testing.T) {
	configureAuthPaths(t)
	SetApiClient(&nonLocalClient{})
	if err := SignInLocal("https://example.invalid"); err == nil {
		t.Fatal("expected non-local server rejection")
	}
}

type nonLocalClient struct{ types.ApiClient }

func (c *nonLocalClient) CreateEmailVerification(email, host, userId string) (*shared.CreateEmailVerificationResponse, *shared.ApiError) {
	return &shared.CreateEmailVerificationResponse{IsLocalMode: false}, nil
}

func assertPrivateAuthFiles(t *testing.T) {
	t.Helper()
	for _, path := range []string{fs.HomeAuthPath, fs.HomeAccountsPath} {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0600 {
			t.Fatalf("%s mode is %o", filepath.Base(path), info.Mode().Perm())
		}
	}
}

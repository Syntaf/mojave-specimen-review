package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	sessionCookie = "mojave_session"
	stateCookie   = "mojave_oauth_state"
	sessionTTL    = 30 * 24 * time.Hour
)

// User is whoever is signed in. Only GitHub logins on the allowlist may write.
type User struct {
	Login  string `json:"login"`
	Avatar string `json:"avatar,omitempty"`
}

// Auth implements GitHub's OAuth web flow by hand. It is about sixty lines of
// HTTP, which is less code than wiring up a library and easier to follow.
type Auth struct {
	cfg     config
	allowed map[string]bool
	secure  bool
}

func newAuth(cfg config) *Auth {
	a := &Auth{cfg: cfg, allowed: map[string]bool{}, secure: strings.HasPrefix(cfg.baseURL, "https://")}
	for _, u := range cfg.allowed {
		a.allowed[u] = true
	}
	return a
}

// enabled reports whether sign-in is configured. Without it the site still
// serves, read-only, which is what lets it go live before an OAuth app exists.
func (a *Auth) enabled() bool {
	return a.cfg.clientID != "" && a.cfg.clientSecret != "" && a.cfg.sessionKey != ""
}

// CanWrite is the single authorization rule: signed in, and on the allowlist if
// one was configured.
func (a *Auth) CanWrite(u *User) bool {
	if u == nil || !a.enabled() {
		return false
	}
	if len(a.allowed) == 0 {
		return true
	}
	return a.allowed[strings.ToLower(u.Login)]
}

func (a *Auth) login(w http.ResponseWriter, r *http.Request) {
	if !a.enabled() {
		http.Error(w, "sign-in is not configured on this server", http.StatusServiceUnavailable)
		return
	}
	state, err := randomToken()
	if err != nil {
		http.Error(w, "could not start sign-in", http.StatusInternalServerError)
		return
	}
	a.setCookie(w, stateCookie, state, 10*time.Minute)

	q := url.Values{
		"client_id":    {a.cfg.clientID},
		"redirect_uri": {a.cfg.baseURL + "/auth/callback"},
		"scope":        {"read:user"},
		"state":        {state},
	}
	http.Redirect(w, r, "https://github.com/login/oauth/authorize?"+q.Encode(), http.StatusFound)
}

func (a *Auth) callback(w http.ResponseWriter, r *http.Request) {
	if !a.enabled() {
		http.Error(w, "sign-in is not configured on this server", http.StatusServiceUnavailable)
		return
	}
	want, err := r.Cookie(stateCookie)
	got := r.URL.Query().Get("state")
	if err != nil || got == "" || subtle.ConstantTimeCompare([]byte(want.Value), []byte(got)) != 1 {
		http.Error(w, "sign-in expired or was tampered with, please try again", http.StatusBadRequest)
		return
	}
	a.clearCookie(w, stateCookie)

	token, err := a.exchangeCode(r.URL.Query().Get("code"))
	if err != nil {
		log.Printf("oauth exchange: %v", err)
		http.Error(w, "GitHub rejected the sign-in", http.StatusBadGateway)
		return
	}
	user, err := fetchGitHubUser(token)
	if err != nil {
		log.Printf("oauth userinfo: %v", err)
		http.Error(w, "could not read your GitHub profile", http.StatusBadGateway)
		return
	}

	a.setCookie(w, sessionCookie, a.sign(user), sessionTTL)
	if !a.CanWrite(user) {
		log.Printf("signed in %s (not on allowlist, read-only)", user.Login)
	}
	http.Redirect(w, r, "/", http.StatusFound)
}

func (a *Auth) logout(w http.ResponseWriter, r *http.Request) {
	a.clearCookie(w, sessionCookie)
	http.Redirect(w, r, "/", http.StatusFound)
}

// User returns the signed-in user, or nil. A bad signature is simply "not
// signed in" — there is nothing useful to tell the caller.
func (a *Auth) User(r *http.Request) *User {
	if !a.enabled() {
		return nil
	}
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return nil
	}
	return a.verify(c.Value)
}

// sign encodes the session as payload.signature. The payload is not secret, so
// it is only signed, not encrypted.
func (a *Auth) sign(u *User) string {
	payload := fmt.Sprintf("%s|%s|%d", u.Login, u.Avatar, time.Now().Add(sessionTTL).Unix())
	b := base64.RawURLEncoding.EncodeToString([]byte(payload))
	return b + "." + a.mac(b)
}

func (a *Auth) verify(v string) *User {
	b, sig, ok := strings.Cut(v, ".")
	if !ok || !hmac.Equal([]byte(sig), []byte(a.mac(b))) {
		return nil
	}
	raw, err := base64.RawURLEncoding.DecodeString(b)
	if err != nil {
		return nil
	}
	parts := strings.Split(string(raw), "|")
	if len(parts) != 3 {
		return nil
	}
	exp, err := strconv.ParseInt(parts[2], 10, 64)
	if err != nil || time.Now().Unix() > exp {
		return nil
	}
	return &User{Login: parts[0], Avatar: parts[1]}
}

func (a *Auth) mac(b string) string {
	m := hmac.New(sha256.New, []byte(a.cfg.sessionKey))
	m.Write([]byte(b))
	return base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

func (a *Auth) exchangeCode(code string) (string, error) {
	if code == "" {
		return "", fmt.Errorf("no code in callback")
	}
	form := url.Values{
		"client_id":     {a.cfg.clientID},
		"client_secret": {a.cfg.clientSecret},
		"code":          {code},
		"redirect_uri":  {a.cfg.baseURL + "/auth/callback"},
	}
	req, err := http.NewRequest(http.MethodPost,
		"https://github.com/login/oauth/access_token", strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var body struct {
		AccessToken string `json:"access_token"`
		Error       string `json:"error_description"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "", err
	}
	if body.AccessToken == "" {
		return "", fmt.Errorf("no access token: %s", body.Error)
	}
	return body.AccessToken, nil
}

func fetchGitHubUser(token string) (*User, error) {
	req, err := http.NewRequest(http.MethodGet, "https://api.github.com/user", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("github returned %s", resp.Status)
	}

	var body struct {
		Login  string `json:"login"`
		Avatar string `json:"avatar_url"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, err
	}
	if body.Login == "" {
		return nil, fmt.Errorf("github returned no login")
	}
	return &User{Login: body.Login, Avatar: body.Avatar}, nil
}

var httpClient = &http.Client{Timeout: 10 * time.Second}

func (a *Auth) setCookie(w http.ResponseWriter, name, value string, ttl time.Duration) {
	http.SetCookie(w, &http.Cookie{
		Name: name, Value: value, Path: "/",
		Expires: time.Now().Add(ttl), MaxAge: int(ttl.Seconds()),
		HttpOnly: true, Secure: a.secure, SameSite: http.SameSiteLaxMode,
	})
}

func (a *Auth) clearCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{
		Name: name, Value: "", Path: "/", MaxAge: -1,
		HttpOnly: true, Secure: a.secure, SameSite: http.SameSiteLaxMode,
	})
}

func randomToken() (string, error) {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

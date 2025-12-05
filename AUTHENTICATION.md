# Authentication Setup

The PG Backup Manager now requires authentication to access the application and all API endpoints.

## Configuration

Create a `.env` file in the project root directory with the following variables:

```env
# Authentication Credentials
# Change these values in production!
AUTH_USERNAME=admin
AUTH_PASSWORD=admin

# Optional: Secret key for session tokens (change in production)
SECRET_KEY=your-secret-key-change-this-in-production
```

## Default Credentials

- **Username**: `admin`
- **Password**: `admin`

⚠️ **IMPORTANT**: Change these default credentials in production!

## How It Works

1. **Login**: Users must login at `/login` before accessing the application
2. **Session Management**: After successful login, a session cookie is set (valid for 24 hours)
3. **API Protection**: All API endpoints (except `/api/login`) require authentication
4. **Automatic Redirect**: Unauthenticated users are automatically redirected to the login page
5. **Logout**: Users can logout using the logout button in the header

## Security Notes

- Session tokens are signed and have a 24-hour expiration
- Passwords are stored in plain text in `.env` (for simplicity)
- In production, consider:
  - Using environment variables instead of `.env` file
  - Enabling HTTPS and setting `secure=True` for cookies
  - Using a stronger secret key
  - Implementing password hashing
  - Adding rate limiting for login attempts

## File Locations

- Authentication logic: `webapp/app.py`
- Login page: Embedded in `webapp/app.py` (served at `/login`)
- Frontend auth handling: `webapp/static/app.js`



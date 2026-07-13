from supabase_client import get_supabase_client

supabase = get_supabase_client()

response = supabase.auth.sign_in_with_otp(
    {
        "email": "andantewu2@gmail.com"
    }
)

print(response)
import asyncio
import httpx
import sys

# Configuration
API_BASE_URL = "http://localhost:8000/api"

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

def print_result(test_name, success, message=""):
    if success:
        print(f"{GREEN}[PASS] {test_name}{RESET} {message}")
    else:
        print(f"{RED}[FAIL] {test_name}{RESET} {message}")

async def test_auth_status(client, token=None):
    """Test /auth/status endpoint"""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = await client.get(f"{API_BASE_URL}/auth/status", headers=headers)
        if resp.status_code == 200:
            print_result("Auth Status Check", True, f"Status: {resp.json()}")
            return True
        elif resp.status_code == 401:
             print_result("Auth Status Check", True, "Correctly returned 401 for unauthenticated")
             return True
        else:
            print_result("Auth Status Check", False, f"Unexpected status: {resp.status_code}")
            return False
    except Exception as e:
        print_result("Auth Status Check", False, f"Error: {e}")
        return False

async def main():
    print("Starting Tenant Isolation Audit Integration Test...")
    print("--------------------------------------------------")
    
    async with httpx.AsyncClient() as client:
        # 1. Test Public Access (Auth Status without token)
        await test_auth_status(client)
        
        # Note: To fully test login blocking and admin scoping, we need valid credentials.
        # This script is a template. In a real CI environment, we would seed a test user.
        
        print("\n[INFO] To verify 'Workspace Disabled' flow manually:")
        print("1. Login to the platform normally.")
        print("2. In the database, set your workspace 'is_active' to False.")
        print("   UPDATE workspaces SET is_active = 0 WHERE id = 'YOUR_WORKSPACE_ID';")
        print("3. Refresh the page. You should be redirected to /disabled.")
        print("4. Click 'Refresh Status' (should fail).")
        print("5. Set 'is_active' back to 1.")
        print("6. Click 'Refresh Status' (should succeed and redirect to /).")
        
        print("\n[INFO] To verify 'Admin Scoping':")
        print("1. Login as Admin.")
        print("2. Call GET /api/admin/users.")
        print("3. Verify all returned users have 'workspace_id' matching your admin's workspace_id.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

#!/usr/bin/env python3



import os

import sys

import subprocess

import venv

import shutil





class Setup:

    def __init__(self):

        self.venv_dir = 'venv'

        self.python_executable = os.path.join(self.venv_dir, 'bin', 'python')

        self.pip_executable = os.path.join(self.venv_dir, 'bin', 'pip')



    def check_venv(self):

        """Check if virtual environment exists"""

        return os.path.exists(self.venv_dir)



    def create_venv(self):

        """Create virtual environment"""

        print("Creating virtual environment...")

        venv.create(self.venv_dir, with_pip=True)



    def install_requirements(self):

        """Install requirements"""

        print("Installing requirements...")

        subprocess.run([self.pip_executable, 'install', '-r', 'requirements.txt'])

    def check_and_create_env(self):
        """Copy .env.example to .env if .env doesn't exist"""
        if not os.path.exists('.env') and os.path.exists('.env.example'):
            print("Creating .env file from .env.example...")
            try:
                shutil.copy('.env.example', '.env')
                print(".env file created successfully")
            except Exception as e:
                print(f"Error creating .env file: {e}")
        elif not os.path.exists('.env.example'):
            print("Warning: .env.example file not found")
        elif os.path.exists('.env'):
            print(".env file already exists")

    def run_script(self, script_name, args=None):

        """Run the specified script"""

        cmd = [self.python_executable, script_name]

        if args:

            cmd.extend(args)

        subprocess.run(cmd)

    def check_and_install_pg_dump(self):
        """Check if pg_dump is installed, and install it if needed"""
        try:
            # Check if pg_dump is available
            result = subprocess.run(['pg_dump', '--version'], check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            print(f"pg_dump is already installed: {result.stdout.strip()}")
        except FileNotFoundError:
            print("pg_dump is not installed. Attempting to install...")
            # Install PostgreSQL client tools based on the operating system
            if sys.platform.startswith('linux'):
                try:
                    # Download the PostgreSQL key and dearmor it
                    key_output = subprocess.check_output(
                        ['wget', '--quiet', '-O', '-', 'https://www.postgresql.org/media/keys/ACCC4CF8.asc'])
                    with open('/etc/apt/trusted.gpg.d/postgresql.gpg', 'wb') as f:
                        subprocess.run(['sudo', 'gpg', '--dearmor', '-'], input=key_output, check=True, stdout=f)
                    # Add PostgreSQL APT repository
                    subprocess.run(['sudo', 'sh', '-c',
                                    'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'],
                                   check=True)
                    subprocess.run(['sudo', 'apt', 'update'], check=True)
                    # Install PostgreSQL 16 client tools
                    subprocess.run(['sudo', 'apt', 'install', '-y', 'postgresql-client-16'], check=True)
                    print("pg_dump installed successfully using apt.")
                except subprocess.CalledProcessError as e:
                    print(f"Failed to install pg_dump using apt: {e}")
                    sys.exit(1)
            elif sys.platform == 'darwin':  # macOS
                try:
                    # Try installing with Homebrew
                    subprocess.run(['brew', 'install', 'postgresql'], check=True)
                    print("pg_dump installed successfully using Homebrew.")
                except subprocess.CalledProcessError as e:
                    print(f"Failed to install pg_dump using Homebrew: {e}")
                    sys.exit(1)
            elif os.name == 'nt':  # Windows
                print("Please install PostgreSQL manually on Windows and add pg_dump to your PATH.")
                sys.exit(1)
            else:
                print("Unsupported operating system. Please install pg_dump manually.")
                sys.exit(1)



    def cleanup(self):

        """Remove virtual environment"""

        if os.path.exists(self.venv_dir):

            print("Removing virtual environment...")

            shutil.rmtree(self.venv_dir)



        # Clean up Python cache files

        for root, dirs, files in os.walk('.'):

            for dir_name in dirs:

                if dir_name == '__pycache__':

                    cache_dir = os.path.join(root, dir_name)

                    print(f"Removing cache directory: {cache_dir}")

                    shutil.rmtree(cache_dir)



            for file_name in files:

                if file_name.endswith('.pyc'):

                    cache_file = os.path.join(root, file_name)

                    print(f"Removing cache file: {cache_file}")

                    os.remove(cache_file)



    def reset(self):

        """Reset everything and create fresh environment"""

        self.cleanup()

        self.create_venv()

        self.install_requirements()





def main():

    setup = Setup()



    if len(sys.argv) < 2:

        print("Usage: python run.py [backup|restore|clean|reset] [database_name]")

        sys.exit(1)



    command = sys.argv[1].lower()



    if command == 'clean':

        setup.cleanup()

        print("Cleanup completed")

        sys.exit(0)



    if command == 'reset':

        setup.reset()

        print("Reset completed")

        sys.exit(0)

    setup.check_and_create_env()
    # Check and install pg_dump if necessary

    setup.check_and_install_pg_dump()



    # Create venv if it doesn't exist

    if not setup.check_venv():

        setup.create_venv()

        setup.install_requirements()



    # Check if requirements are installed

    try:

        subprocess.run([setup.pip_executable, 'freeze'], capture_output=True, text=True)

    except Exception:

        setup.install_requirements()



    # Run the appropriate script
    # In main():
    if command == 'backup':
        setup.run_script('backup.py', sys.argv[2:])  # Pass all remaining arguments
    elif command == 'restore':
        setup.run_script('restore.py', sys.argv[2:])  # Pass all remaining arguments

    else:

        print("Invalid command. Use 'backup', 'restore', 'clean', or 'reset'")

        sys.exit(1)





if __name__ == "__main__":

    main()
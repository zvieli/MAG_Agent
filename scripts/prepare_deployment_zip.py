import os
import subprocess
import zipfile
from pathlib import Path

# Paths to include directly (Whitelisted)
roots_to_include = ["host.json", "requirements.txt", "function_app.py"]

# Folders to traverse (Whitelisted)
folders_to_include = ["src", "prompts", "schemas"]

# Output zip name
zip_name = "deployment.zip"

def build_spfx_solution(project_dir: Path):
    spfx_dir = project_dir / "frontend/mag-spfx"
    if not spfx_dir.exists():
        print("Warning: frontend/mag-spfx directory not found. Skipping SPFx build.")
        return

    print("Building and packaging SPFx SharePoint solution using Node 22...")
    # Sourcing NVM and selecting Node 22, which is compatible with SPFx gulp tasks
    cmd = "source $HOME/.nvm/nvm.sh && nvm use 22 && npx gulp bundle --ship && npx gulp package-solution --ship"
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=spfx_dir,
        executable="/bin/bash",
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print("Error during SPFx solution packaging:")
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("SPFx build failed")
    
    sppkg_file = spfx_dir / "sharepoint/solution/mag-spfx.sppkg"
    if sppkg_file.exists():
        import shutil
        target_sppkg = project_dir / "mag-spfx.sppkg"
        shutil.copy(sppkg_file, target_sppkg)
        print(f"SPFx solution successfully packaged at: {sppkg_file}")
        print(f"Copied package to repository root: {target_sppkg}\n")
    else:
        print("Warning: Build succeeded but .sppkg file was not found.\n")

def create_deployment_zip():
    # Dynamically determine the repository root
    project_dir = Path(__file__).resolve().parents[1]
    
    # 1. Build and package the SPFx SharePoint solution
    try:
        build_spfx_solution(project_dir)
    except Exception as e:
        print(f"SPFx build step failed: {e}")
        print("Aborting deployment package creation.")
        return

    zip_path = project_dir / zip_name
    
    # Remove existing zip if it exists
    if zip_path.exists():
        zip_path.unlink()
        
    print(f"Creating deployment package at: {zip_path}")
    
    added_count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 2. Add root files
        for filename in roots_to_include:
            file_path = project_dir / filename
            if file_path.exists():
                zipf.write(file_path, filename)
                print(f"Added: {filename}")
                added_count += 1
            else:
                print(f"Warning: {filename} not found!")

        # 3. Add directories
        for folder in folders_to_include:
            dir_path = project_dir / folder
            if not dir_path.exists():
                print(f"Warning: Folder {folder} not found!")
                continue
                
            for root, dirs, files in os.walk(dir_path):
                rel_root = Path(root).relative_to(project_dir)
                
                # Exclude python cache
                if "__pycache__" in dirs:
                    dirs.remove("__pycache__")
                
                for file in files:
                    file_path = Path(root) / file
                    # Exclude compiled python files
                    if file.endswith(".pyc") or file.endswith(".pyo"):
                        continue
                        
                    archive_name = str(file_path.relative_to(project_dir))
                    zipf.write(file_path, archive_name)
                    print(f"Added: {archive_name}")
                    added_count += 1

    print(f"\nSuccessfully created {zip_path.name} with {added_count} files ({zip_path.stat().st_size / 1024:.1f} KB)")

if __name__ == "__main__":
    create_deployment_zip()

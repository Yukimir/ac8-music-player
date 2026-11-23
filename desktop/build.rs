fn main() {
    println!("cargo:rerun-if-changed=ui");
    println!("cargo:rerun-if-changed=icons/player.ico");
    tauri_build::build()
}

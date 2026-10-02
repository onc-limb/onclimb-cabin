//! household — 夫婦の家計台帳の精算 CLI。詳細は README.md。

mod cli;
mod clock;
mod ledger;
mod model;
mod paths;
mod sqlite_export;
mod store;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match cli::run(&args) {
        Ok(message) => println!("{message}"),
        Err(error) => {
            eprintln!("エラー: {error}");
            std::process::exit(1);
        }
    }
}

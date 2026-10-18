use bevy_render_view::Presentation;
use std::path::PathBuf;

fn main() {
    if let Err(error) = run() { eprintln!("REFUSED: {error}"); std::process::exit(2); }
}
fn run() -> Result<(), String> {
    let mut summary = false;
    let mut frames = None;
    let mut path = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--summary" => summary = true,
            "--frames" => frames=Some(args.next().ok_or("--frames requires a count")?.parse::<u32>().map_err(|_|"invalid frame count")?),
            "--help" | "-h" => { println!("bevy-render-view [--summary] [--frames N] PATH/render.json"); return Ok(()); }
            _ if arg.starts_with('-') => return Err(format!("unknown option {arg}")),
            _ if path.is_none() => path=Some(PathBuf::from(arg)),
            _ => return Err("only one render input is supported".into()),
        }
    }
    if frames.is_some_and(|n| n==0 || n>10000) { return Err("frame count must be 1..10000".into()); }
    let presentation=Presentation::load(&path.ok_or("a render JSON path is required")?)?;
    if summary { println!("{}",serde_json::to_string_pretty(&presentation).map_err(|e|e.to_string())?); return Ok(()); }
    #[cfg(feature="viewer")]
    { viewer::show(presentation,frames); Ok(()) }
    #[cfg(not(feature="viewer"))]
    { let _=frames; Err("window support requires the viewer feature; use --summary".into()) }
}

#[cfg(feature="viewer")]
mod viewer {
    use super::*;
    use bevy::{app::AppExit, core_pipeline::tonemapping::Tonemapping, prelude::*, text::FontSize};
    #[derive(Resource)] struct View { data: Presentation, frames: Option<u32> }
    pub fn show(data: Presentation,frames: Option<u32>) {
        App::new().insert_resource(View{data,frames})
            .insert_resource(ClearColor(Color::srgb_u8(12,21,33)))
            .add_plugins(DefaultPlugins.set(WindowPlugin {
                primary_window:Some(Window{title:"NCP — retained values".into(),..default()}),..default()
            }))
            .add_systems(Startup,setup).add_systems(Update,(draw,finish)).run();
    }
    fn setup(mut commands:Commands,view:Res<View>) {
        commands.spawn((Camera3d::default(),Tonemapping::None,Transform::from_xyz(4.,3.,7.).looking_at(Vec3::ZERO,Vec3::Y)));
        let display = |text:&str| text.replace("retained_runtime_report_requires_fresh_verification", "HISTORICAL — retained report requires fresh verification").replace('_', " ");
        let source=view.data.source.strip_prefix("HOST_FROM_OWNED_CONFIG::").map(|text|format!("Owned teaching input · {}",text.replace("::", " · "))).unwrap_or_else(||view.data.source.clone());
        let mut lines=vec!["NCP · Retained engineering values".into(),display(&view.data.status),display(&view.data.verification),source,view.data.caption.clone()];
        lines.extend(view.data.cards.iter().map(|text|display(text)));
        commands.spawn((Text::new(lines.join("\n\n")),TextFont{font_size:FontSize::Px(15.),..default()},
            TextColor(Color::srgb_u8(220,230,241)),Node{position_type:PositionType::Absolute,left:px(18),top:px(18),width:percent(39),..default()}));
    }
    fn draw(mut gizmos:Gizmos,view:Res<View>) {
        let point=|v:&[f64;3]|Vec3::new(v[0] as f32,v[1] as f32,v[2] as f32);
        let points:Vec<Vec3>=view.data.paths.iter().flat_map(|p|p.points.iter().map(point)).collect();
        let low=points.iter().copied().reduce(Vec3::min).unwrap_or(Vec3::ZERO);
        let high=points.iter().copied().reduce(Vec3::max).unwrap_or(Vec3::ONE);
        let center=(low+high)*0.5;
        let scale=3.0/(high-low).length().max(1.);
        for (index,path) in view.data.paths.iter().enumerate() {
            let color=if index%2==0 {Color::srgb_u8(96,223,205)} else {Color::srgb_u8(127,155,184)};
            gizmos.linestrip(path.points.iter().map(|v|(point(v)-center)*scale+Vec3::X*1.2),color);
        }
    }
    fn finish(mut view:ResMut<View>,keys:Res<ButtonInput<KeyCode>>,mut exit:MessageWriter<AppExit>) {
        if keys.just_pressed(KeyCode::Escape) { exit.write(AppExit::Success); }
        if let Some(frames)=view.frames.as_mut() {
            *frames-=1;
            if *frames==0 { println!("NCP_PROJECTOR_FRAMES_COMPLETE"); exit.write(AppExit::Success); }
        }
    }
}

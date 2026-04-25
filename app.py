import streamlit as st
import os
import subprocess
import glob
from PIL import Image
import tempfile
import time

st.set_page_config(
    page_title="Ultimate Match Dashboard",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for premium UI
st.markdown("""
<style>
    .main {
        background-color: #f8f9fa;
        font-family: 'Inter', sans-serif;
    }
    h1 {
        color: #1a202c;
        font-weight: 800;
        letter-spacing: -1px;
    }
    .stButton>button {
        background-color: #ff4b44;
        color: white;
        border-radius: 8px;
        padding: 0.75rem 1.5rem;
        font-weight: 600;
        border: none;
        transition: all 0.2s ease;
        width: 100%;
    }
    .stButton>button:hover {
        background-color: #e53e3e;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(255, 75, 68, 0.2);
    }
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }
</style>
""", unsafe_allow_html=True)

st.title("⚽ Ultimate Post-Match Dashboard")
st.markdown("Upload your match data and instantly generate professional-grade tactical and player analysis dashboards.")

# Sidebar for inputs
with st.sidebar:
    st.header("📊 Match Data Configuration")
    
    st.markdown("### 1. Upload Match HTML")
    uploaded_file = st.file_uploader("Upload WhoScored HTML file", type=['html'])
    
    st.markdown("### 2. Match Stats")
    col1, col2 = st.columns(2)
    with col1:
        home_xg = st.number_input("Home xG", min_value=0.0, max_value=10.0, value=0.89, step=0.01)
        home_xgot = st.number_input("Home xGOT", min_value=0.0, max_value=10.0, value=0.56, step=0.01)
    with col2:
        away_xg = st.number_input("Away xG", min_value=0.0, max_value=10.0, value=1.09, step=0.01)
        away_xgot = st.number_input("Away xGOT", min_value=0.0, max_value=10.0, value=1.13, step=0.01)

    generate_btn = st.button("Generate Dashboards 🚀")

# Function to get latest images
def get_latest_images():
    output_dir = 'match report analysis'
    if not os.path.exists(output_dir):
        return None, None
    
    files = glob.glob(os.path.join(output_dir, '*.png'))
    if not files:
        return None, None
    
    files.sort(key=os.path.getmtime, reverse=True)
    
    # We expect 2 dashboards per run (Dashboard and Player_Dashboard)
    dashboard_img = None
    player_dashboard_img = None
    
    for f in files[:4]:  # look at recent files
        if 'Player_Dashboard' in f:
            if not player_dashboard_img:
                player_dashboard_img = f
        elif 'Dashboard' in f:
            if not dashboard_img:
                dashboard_img = f
                
    return dashboard_img, player_dashboard_img

# Main area
if generate_btn:
    if not uploaded_file:
        st.error("⚠️ Please upload the match HTML file first.")
    else:
        with st.spinner("Analyzing match data and rendering graphics... This may take a minute."):
            try:
                # Save uploaded file to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name
                # Construct command
                import sys
                cmd = [
                    sys.executable, 
                    "PostMatchDashboard.py", 
                    tmp_path, 
                    str(home_xg), 
                    str(away_xg), 
                    str(home_xgot), 
                    str(away_xgot)
                ]
                
                # Execute
                process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                stdout, stderr = process.communicate()
                
                # Cleanup temp file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
                
                if process.returncode != 0:
                    st.error("An error occurred during analysis.")
                    with st.expander("View Error Details"):
                        st.code(stderr)
                else:
                    st.success("Dashboards generated successfully!")
                    
                    # Fetch and display the images
                    dash, p_dash = get_latest_images()
                    
                    if dash and p_dash:
                        tab1, tab2 = st.tabs(["🗺️ Match Dashboard", "👤 Player Dashboard"])
                        
                        with tab1:
                            st.image(Image.open(dash), use_container_width=True)
                        
                        with tab2:
                            st.image(Image.open(p_dash), use_container_width=True)
                    else:
                        st.warning("Analysis completed, but could not locate the output images.")
                        
            except Exception as e:
                st.error(f"Execution failed: {str(e)}")
else:
    # Display instructions or placeholders when not generating
    st.info("👈 Please configure your match parameters in the sidebar and click Generate to begin.")
    
    # Check if there are already existing images to show
    dash, p_dash = get_latest_images()
    if dash and p_dash:
        st.markdown("### Previously Generated Dashboards")
        tab1, tab2 = st.tabs(["🗺️ Match Dashboard", "👤 Player Dashboard"])
        with tab1:
            st.image(Image.open(dash), use_container_width=True)
        with tab2:
            st.image(Image.open(p_dash), use_container_width=True)
